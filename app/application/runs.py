"""HTTP、CLI 等入口共用的运行应用服务。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Callable
from uuid import uuid4

from app.bootstrap.runtime import ProjectOSContainer, build_container
from app.orchestration.runner import GraphRunResult, GraphRunStatus
from app.orchestration.state import RunState
from app.planner.service import PlannerFailure


ContainerBuilder = Callable[[str], ProjectOSContainer]


@dataclass(frozen=True)
class StartedRun:
    trace_id: str
    plan_id: str
    workflow_id: str
    status: str


class RunCoordinator:
    """进程内后台执行器。

    Trace 是持久化事实来源；Future 只用于当前 API 进程展示 ``queued/running`` 状态。
    未来替换为队列系统时，RunService 的 API 契约无需改变。
    """

    def __init__(self, *, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="projectos-run"
        )
        self._futures: dict[str, Future[GraphRunResult]] = {}
        self._lock = Lock()

    def submit(
        self,
        container: ProjectOSContainer,
        plan,
        *,
        state: RunState | None = None,
    ) -> None:
        future = self._executor.submit(self._run_with_repairs, container, plan, state)
        with self._lock:
            self._futures[plan.trace.trace_id] = future

    def status(self, trace_id: str) -> str | None:
        with self._lock:
            future = self._futures.get(trace_id)
        if future is None:
            return None
        if not future.done():
            return "running"
        if future.cancelled():
            return "cancelled"
        try:
            return future.result().status.value
        except Exception:
            return "failed"

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _run_with_repairs(
        container: ProjectOSContainer,
        plan,
        state: RunState | None = None,
    ) -> GraphRunResult:
        result = container.runner.run(plan, state=state)
        for repair_attempt in range(1, 3):
            if result.status is not GraphRunStatus.NEEDS_REPLAN:
                break
            if result.failure_signal is None:
                break
            planning = container.planner.plan_repair(
                previous_plan=plan,
                failure=result.failure_signal,
                plan_id=f"{plan.id}-repair-{repair_attempt}",
            )
            repair_result = container.runner.run(planning.plan)
            if repair_result.status is not GraphRunStatus.COMPLETED:
                result = repair_result
                break
            # 修复成功后从原计划 checkpoint 恢复，执行剩余未完成节点（如 review），
            # 避免交付链在修复后绕过质量门直接完成。
            checkpoint = container.traces.load_checkpoint(plan.trace.trace_id)
            state = RunState.from_checkpoint(plan, checkpoint)
            result = container.runner.run(plan, state=state)
            if result.status is not GraphRunStatus.NEEDS_REPLAN:
                break
        return result


class RunService:
    """创建受控 Workflow 的计划并将其提交给后台执行器。"""

    def __init__(
        self,
        *,
        coordinator: RunCoordinator,
        container_builder: ContainerBuilder = build_container,
    ) -> None:
        self._coordinator = coordinator
        self._container_builder = container_builder

    def start_controlled_workflow(
        self, *, project_path: str, goal: str, workflow_id: str
    ) -> StartedRun:
        container = self._container_builder(project_path)
        plan_id = f"run-{uuid4().hex[:12]}"
        try:
            template = container.templates.get(workflow_id)
            if template is None:
                raise PlannerFailure(f"未注册 Workflow: '{workflow_id}'")
            required_artifacts = {
                artifact_key
                for node in template.nodes
                for artifact_key in node.input_artifacts
            }
            produced_artifacts = {
                node.artifact_key or node.publish_target or node.output_key
                for node in template.nodes
            }
            missing = sorted(
                artifact_key
                for artifact_key in required_artifacts
                if artifact_key not in produced_artifacts
                and not container.artifacts.exists(artifact_key)
            )
            if missing:
                raise PlannerFailure(
                    "Workflow 缺少已发布前置产物: " + ", ".join(missing)
                )
            planning = container.planner.plan_controlled_workflow(
                goal=goal, plan_id=plan_id, workflow_id=workflow_id
            )
        except PlannerFailure:
            raise
        container.traces.record_plan_baseline(planning.plan)
        self._coordinator.submit(container, planning.plan)
        return StartedRun(
            trace_id=planning.plan.trace.trace_id,
            plan_id=planning.plan.id,
            workflow_id=workflow_id,
            status="running",
        )

    def start_dynamic_plan(
        self, *, project_path: str, goal: str, plan_id: str
    ) -> StartedRun:
        """为连续会话执行一次普通 Planner 计划，不接受调用方注入权限字段。"""
        container = self._container_builder(project_path)
        planning = container.planner.plan(goal=goal, plan_id=plan_id)
        container.traces.record_plan_baseline(planning.plan)
        self._coordinator.submit(container, planning.plan)
        return StartedRun(
            trace_id=planning.plan.trace.trace_id,
            plan_id=planning.plan.id,
            workflow_id=planning.plan.template_id or "dynamic",
            status="running",
        )

    def start_patch_plan(
        self,
        *,
        project_path: str,
        trace_id: str,
        change_request: str,
    ) -> StartedRun:
        """在原 Trace checkpoint 上应用局部 PlanPatch，只重跑受影响子图。"""
        container = self._container_builder(project_path)
        trace = container.traces.load_trace(trace_id)
        status = str(trace.get("status", ""))
        if status in {"planned", "running"}:
            raise PlannerFailure("当前 Trace 仍在运行，不能应用局部修改")
        previous_plan = container.traces.load_plan(trace_id)
        try:
            checkpoint = container.traces.load_checkpoint(trace_id)
            state = RunState.from_checkpoint(previous_plan, checkpoint)
        except FileNotFoundError:
            state = RunState(plan=previous_plan)
        completed_ids = set(state.node_results)
        patching = container.planner.plan_patch(
            previous_plan=previous_plan,
            change_request=change_request,
            completed_work_item_ids=completed_ids,
            allow_completed_revision=True,
        )
        old_items = {item.id: item for item in previous_plan.work_items}
        invalidated = set(patching.invalidated_work_item_ids)
        state.node_results = {
            item_id: result
            for item_id, result in state.node_results.items()
            if item_id not in invalidated and patching.plan.work_item(item_id) is not None
        }
        invalidated_output_keys = {
            old_items[item_id].output_key
            for item_id in invalidated
            if item_id in old_items
        }
        state.artifacts = {
            key: value
            for key, value in state.artifacts.items()
            if key not in invalidated_output_keys
        }
        state.plan = patching.plan
        container.traces.record_plan_baseline(patching.plan)
        container.traces.record_plan(patching.plan)
        container.traces.record_event(
            patching.plan.trace,
            "control",
            "plan_patch_applied",
            details={
                "base_plan_id": previous_plan.id,
                "new_plan_id": patching.plan.id,
                "invalidated_work_item_ids": sorted(invalidated),
                "added_work_item_ids": list(patching.added_work_item_ids),
                "removed_work_item_ids": list(patching.removed_work_item_ids),
            },
        )
        self._coordinator.submit(container, patching.plan, state=state)
        return StartedRun(
            trace_id=trace_id,
            plan_id=patching.plan.id,
            workflow_id=patching.plan.template_id or "dynamic",
            status="running",
        )

    def controlled_workflows(self, project_path: str) -> tuple[dict[str, str], ...]:
        container = self._container_builder(project_path)
        return tuple(
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
            }
            for template in container.templates.templates()
            if template.has_controlled_execution
        )

    def resume_run(
        self, *, project_path: str, trace_id: str, source_name: str | None = None
    ) -> StartedRun:
        """从最近 checkpoint 恢复；可在同一恢复动作中批准一个动态来源。"""
        container = self._container_builder(project_path)
        trace = container.traces.load_trace(trace_id)
        status = str(trace.get("status", ""))
        resumable = {
            GraphRunStatus.FAILED.value,
            GraphRunStatus.BLOCKED.value,
            GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL.value,
            GraphRunStatus.NEEDS_REPLAN.value,
        }
        # API 进程重启后，Trace 持久化状态可能仍是 planned/running，但原进程
        # 的 Future 已经不存在。此时允许从 checkpoint 恢复，避免把一条可恢复
        # 的交付链永久遗留为“运行中”。同一进程仍在执行时必须拒绝重复提交。
        if status in {"planned", "running"}:
            if self._coordinator.status(trace_id) is not None:
                raise PlannerFailure("Trace 仍在当前进程运行，不能重复恢复")
            resumable.add(status)
        if status not in resumable:
            raise PlannerFailure(f"Trace 当前状态不可恢复: {status}")
        if status == GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL.value and source_name is None:
            raise PlannerFailure("当前 Trace 等待能力审批，请先调用 capabilities/approve")
        plan = container.traces.load_plan(trace_id)
        try:
            checkpoint = container.traces.load_checkpoint(trace_id)
            state = RunState.from_checkpoint(plan, checkpoint)
        except FileNotFoundError:
            # 进程可能在首个节点写 checkpoint 前退出；从空状态重放整条计划。
            state = RunState(plan=plan)
        if source_name is not None:
            waiting = next(
                (
                    event
                    for event in reversed(container.traces.list_events(trace_id))
                    if event.get("type") == "work_item_waiting_capability"
                ),
                None,
            )
            if waiting is None:
                raise PlannerFailure("当前 Trace 没有待批准的能力请求")
            work_item = plan.work_item(str(waiting.get("work_item_id", "")))
            if work_item is None:
                raise PlannerFailure("能力请求对应的 WorkItem 不存在")
            definition = container.agents.definition(work_item.agent_id)
            if definition is None:
                raise PlannerFailure(f"能力请求 Agent 未注册: {work_item.agent_id}")
            capability = str(waiting.get("details", {}).get("capability", ""))
            candidates = container.gateway.find_sources_for_capability(
                definition.domain, capability
            )
            candidate = next(
                (source for source in candidates if source.source_name == source_name),
                None,
            )
            if candidate is None:
                raise PlannerFailure(
                    f"来源 '{source_name}' 不能满足能力 '{capability}'"
                )
            # 一次审批覆盖交付链：在提供该能力的全部 domain 下激活，
            # 后续领域节点（如 code/review）无需再次等待审批。
            container.gateway.activate_source_for_capability(capability, source_name)
            container.traces.record_event(
                plan.trace,
                "control",
                "capability_approved",
                details={
                    "source_name": source_name,
                    "capability": capability,
                    "work_item_id": work_item.id,
                },
            )
        self._coordinator.submit(container, plan, state=state)
        return StartedRun(
            trace_id=trace_id,
            plan_id=plan.id,
            workflow_id=plan.template_id or "",
            status="running",
        )

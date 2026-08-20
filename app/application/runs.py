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
            plan = planning.plan
            result = container.runner.run(plan)
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
        self._coordinator.submit(container, planning.plan)
        return StartedRun(
            trace_id=planning.plan.trace.trace_id,
            plan_id=planning.plan.id,
            workflow_id=workflow_id,
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

    def resume_run(self, *, project_path: str, trace_id: str) -> StartedRun:
        """从最近 checkpoint 恢复未完成节点；不重新规划目标或权限。"""
        container = self._container_builder(project_path)
        trace = container.traces.load_trace(trace_id)
        status = str(trace.get("status", ""))
        resumable = {
            GraphRunStatus.FAILED.value,
            GraphRunStatus.BLOCKED.value,
            GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL.value,
            GraphRunStatus.NEEDS_REPLAN.value,
        }
        if status not in resumable:
            raise PlannerFailure(f"Trace 当前状态不可恢复: {status}")
        plan = container.traces.load_plan(trace_id)
        checkpoint = container.traces.load_checkpoint(trace_id)
        state = RunState.from_checkpoint(plan, checkpoint)
        self._coordinator.submit(container, plan, state=state)
        return StartedRun(
            trace_id=trace_id,
            plan_id=plan.id,
            workflow_id=plan.template_id or "",
            status="running",
        )

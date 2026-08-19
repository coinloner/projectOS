from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from threading import Lock

from app.agent.registry import AgentRegistry
from app.artifact.repository import ArtifactRepository
from app.memory.context import MemoryContextAssembler
from app.memory.store import MemoryStore
from app.tool_manager.gateway import ToolGateway
from app.orchestration.node_result import NodeResult, NodeStatus
from app.orchestration.plan import ExecutionPlan
from app.orchestration.state import RunState
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import WorkItem
from app.orchestration.retry import (
    FailureKind,
    FailureSignal,
    RecoveryAction,
    RetryPolicy,
    sandbox_failure_signal,
)
from app.orchestration.task_input import build_task_input


def _agent_result_text(result) -> str:
    return str(result)


class GraphRunStatus(str, Enum):
    COMPLETED = "completed"
    WAITING_FOR_CAPABILITY_APPROVAL = "waiting_for_capability_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    NEEDS_REPLAN = "needs_replan"


@dataclass(frozen=True)
class SourceCandidate:
    """可满足能力缺口的来源摘要。"""

    name: str
    capability: str


@dataclass(frozen=True)
class GraphRunResult:
    """GraphRunner 一次同步执行后交给调用方的状态。"""

    status: GraphRunStatus
    state: RunState
    node_result: NodeResult | None = None
    candidate_sources: tuple[SourceCandidate, ...] = ()
    failure_signal: FailureSignal | None = None
    error: str | None = None


class GraphRunner:
    """ExecutionPlan 的通用同步执行器。

    Runner 只理解节点依赖、Agent 注册、产物可用性和能力升级等待；它不理解
    requirement.md、代码文件或其他领域业务。领域产物的正文由 Agent 通过其受限
    ``load_artifact`` 工具按需读取，避免每个节点重复注入长文档。
    """

    def __init__(
        self,
        agents: AgentRegistry,
        tools: ToolGateway,
        *,
        traces: TraceStore | None = None,
        max_workers: int = 4,
        retry_policy: RetryPolicy | None = None,
        artifacts: ArtifactRepository | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("GraphRunner.max_workers 至少为 1")
        self._agents = agents
        self._tools = tools
        self._traces = traces
        self._max_workers = max_workers
        self._retry_policy = retry_policy or RetryPolicy()
        self._artifacts = artifacts
        self._memory = memory
        self._memory_context = (
            MemoryContextAssembler(memory) if memory is not None else None
        )
        self._retry_lock = Lock()
        self._total_retries = 0

    def run(self, plan: ExecutionPlan) -> GraphRunResult:
        with self._retry_lock:
            self._total_retries = 0
        state = RunState(plan=plan)
        if self._traces is not None:
            self._traces.record_plan(plan)

        while not state.is_complete():
            ready_items = state.ready_items()
            if not ready_items:
                result = GraphRunResult(
                    status=GraphRunStatus.FAILED,
                    state=state,
                    error="没有可执行节点，计划依赖未能推进",
                )
                self._finish_trace(plan, result)
                return result

            scheduled_items = self._select_schedulable_items(ready_items)
            for item in scheduled_items:
                self._record_event(plan, item, "work_item_started")
            results = self._run_ready_items(state, scheduled_items)
            for item in scheduled_items:
                result = results[item.id]
                state.record(item, result)
                self._record_result(plan, item, result)
                self._record_checkpoint(state)

                if result.status is NodeStatus.FAILED:
                    graph_result = GraphRunResult(
                        status=GraphRunStatus.FAILED,
                        state=state,
                        node_result=result,
                        error=result.error,
                    )
                    self._finish_trace(plan, graph_result)
                    return graph_result
                if result.status is NodeStatus.NEEDS_CAPABILITY:
                    graph_result = self._handle_capability_request(state, result)
                    self._finish_trace(plan, graph_result)
                    return graph_result
                if result.status is NodeStatus.NEEDS_REPLAN:
                    if (
                        result.failure_signal is not None
                        and result.failure_signal.kind is FailureKind.SANDBOX_SETUP
                    ):
                        graph_result = GraphRunResult(
                            status=GraphRunStatus.BLOCKED,
                            state=state,
                            node_result=result,
                            failure_signal=result.failure_signal,
                            error=result.failure_signal.summary,
                        )
                        self._finish_trace(plan, graph_result)
                        return graph_result
                    graph_result = GraphRunResult(
                        status=GraphRunStatus.NEEDS_REPLAN,
                        state=state,
                        node_result=result,
                        failure_signal=result.failure_signal,
                        error=result.failure_signal.summary
                        if result.failure_signal is not None
                        else "节点请求重新规划",
                    )
                    self._finish_trace(plan, graph_result)
                    return graph_result

        graph_result = GraphRunResult(status=GraphRunStatus.COMPLETED, state=state)
        self._finish_trace(plan, graph_result)
        return graph_result

    def _select_schedulable_items(
        self, ready_items: tuple[WorkItem, ...]
    ) -> tuple[WorkItem, ...]:
        """在 Agent 合同的并发上限内选择本轮可执行节点。"""
        selected: list[WorkItem] = []
        running_by_agent: dict[str, int] = {}
        for item in ready_items:
            definition = self._agents.definition(item.agent_id)
            limit = definition.max_parallel_instances if definition is not None else 1
            if running_by_agent.get(item.agent_id, 0) >= limit:
                continue
            selected.append(item)
            running_by_agent[item.agent_id] = running_by_agent.get(item.agent_id, 0) + 1
            if len(selected) == self._max_workers:
                break
        return tuple(selected)

    def _run_ready_items(
        self, state: RunState, items: tuple[WorkItem, ...]
    ) -> dict[str, NodeResult]:
        if len(items) == 1:
            item = items[0]
            return {item.id: self._run_item_with_retries(state, item)}

        with ThreadPoolExecutor(max_workers=len(items)) as executor:
            futures = {
                item.id: executor.submit(self._run_item_with_retries, state, item)
                for item in items
            }
            return {item.id: futures[item.id].result() for item in items}

    def _run_item_with_retries(self, state: RunState, item: WorkItem) -> NodeResult:
        item_retries = 0
        retries_by_kind: dict[FailureKind, int] = {}
        while True:
            attempt = item_retries + 1
            result = self._run_item(state, item, attempt=attempt)
            signal = self._failure_signal_for(result)
            if signal is None:
                return result
            action = self._recovery_action(
                signal,
                item_retries=item_retries,
                kind_retries=retries_by_kind.get(signal.kind, 0),
            )
            if action is RecoveryAction.RETRY_ITEM:
                item_retries += 1
                retries_by_kind[signal.kind] = retries_by_kind.get(signal.kind, 0) + 1
                self._record_event(
                    state.plan,
                    item,
                    "work_item_retrying",
                    details={"kind": signal.kind.value, "attempt": item_retries + 1},
                )
                continue
            if action is RecoveryAction.REQUEST_REPLAN:
                return NodeResult.needs_replan(
                    node_id=item.id, agent_id=item.agent_id, signal=signal
                )
            if action is RecoveryAction.BLOCK:
                return NodeResult.needs_replan(
                    node_id=item.id, agent_id=item.agent_id, signal=signal
                )
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=(
                    f"工作项 '{item.id}' 的 {signal.kind.value} 重试额度已耗尽: "
                    f"{signal.summary}"
                ),
            )

    def _recovery_action(
        self,
        signal: FailureSignal,
        *,
        item_retries: int,
        kind_retries: int,
    ) -> RecoveryAction:
        with self._retry_lock:
            action = self._retry_policy.action_for(
                signal,
                total_retries=self._total_retries,
                item_retries=item_retries,
                kind_retries=kind_retries,
            )
            if action is RecoveryAction.RETRY_ITEM:
                self._total_retries += 1
            return action

    @staticmethod
    def _failure_signal_for(result: NodeResult) -> FailureSignal | None:
        if result.status is NodeStatus.NEEDS_REPLAN:
            return result.failure_signal
        if result.status is NodeStatus.FAILED:
            return FailureSignal(FailureKind.AGENT_RUNTIME, result.error or "Agent 执行失败")
        return None

    def _run_item(
        self, state: RunState, item: WorkItem, *, attempt: int = 1
    ) -> NodeResult:
        if item.execution_mode is ExecutionMode.QUALITY_GATE:
            return self._run_quality_gate(state, item)
        if item.agent_id == "task_agent" and item.execution_mode not in {
            ExecutionMode.PARTITIONED,
            ExecutionMode.INTEGRATION,
        }:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error="TaskAgent 只能通过 PARTITIONED 或 INTEGRATION 标准执行方式运行",
            )
        definition = self._agents.definition(item.agent_id)
        if definition is None:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=f"ExecutionPlan 引用了未注册 Agent: '{item.agent_id}'",
            )

        try:
            context = ExecutionContext(
                trace_id=state.plan.trace.trace_id,
                work_item_id=item.id,
                agent_id=item.agent_id,
                execution_mode=item.execution_mode,
                input_refs=item.input_refs,
                output_slot=item.output_slot,
                publish_target=item.publish_target,
                memory=self._memory,
            )
            task_input = build_task_input(state, item)
            memory_context = (
                self._memory_context.build(
                    trace_id=state.plan.trace.trace_id,
                    work_item_id=item.id,
                    query=(
                        f"{item.objective} {' '.join(item.acceptance_criteria)} "
                        f"{' '.join(item.constraints)}"
                    ),
                ).as_prompt()
                if self._memory_context is not None
                else ""
            )
            prompt = task_input.as_prompt(memory_context=memory_context)
            self._record_memory(
                context,
                role="system",
                event_type="agent_input",
                content=prompt,
                attempt=attempt,
            )
            agent_result = self._agents.create(item.agent_id).run(
                prompt, context=context
            )
            self._record_memory(
                context,
                role="assistant",
                event_type="agent_output",
                content=agent_result.content or _agent_result_text(agent_result),
                attempt=attempt,
                metadata={"status": agent_result.status.value},
            )
        except Exception as error:
            if "context" in locals():
                self._record_memory(
                    context,
                    role="control",
                    event_type="agent_error",
                    content=str(error),
                    attempt=attempt,
                )
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=f"工作项 '{item.id}' 执行失败: {error}",
            )

        node_result = NodeResult.from_agent_result(
            node_id=item.id,
            agent_id=item.agent_id,
            result=agent_result,
        )
        if node_result.status is not NodeStatus.COMPLETED or self._traces is None:
            return node_result
        if definition.domain != "test":
            return node_result
        evidence = self._traces.latest_sandbox_evidence(context)
        if evidence is None:
            return NodeResult.needs_replan(
                node_id=item.id,
                agent_id=item.agent_id,
                signal=FailureSignal(
                    FailureKind.TEST_EVIDENCE_MISSING,
                    "TestAgent 未产生当前 WorkItem 的 SandboxEvidence",
                ),
            )
        signal = sandbox_failure_signal(
            status=evidence.status,
            evidence_id=evidence.id,
            message=evidence.message,
        )
        if signal is None:
            return node_result
        return NodeResult.needs_replan(
            node_id=item.id, agent_id=item.agent_id, signal=signal
        )

    def _record_memory(
        self,
        context: ExecutionContext,
        *,
        role: str,
        event_type: str,
        content: str,
        attempt: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if self._memory is None or not content.strip():
            return
        self._memory.append(
            trace_id=context.trace_id,
            role=role,
            event_type=event_type,
            content=content,
            work_item_id=context.work_item_id,
            agent_id=context.agent_id,
            attempt=attempt,
            metadata=metadata,
        )

    def _record_checkpoint(self, state: RunState) -> None:
        if self._memory is None:
            return
        self._memory.checkpoint(
            state.plan.trace.trace_id,
            state={
                "plan_id": state.plan.id,
                "completed_work_items": sorted(state.node_results),
                "pending_work_items": [
                    item.id
                    for item in state.plan.work_items
                    if item.id not in state.node_results
                ],
                "artifact_keys": sorted(state.artifacts),
            },
        )

    def _run_quality_gate(self, state: RunState, item: WorkItem) -> NodeResult:
        """质量门由控制面执行，不依赖模型决定是否发布。"""
        if self._artifacts is None:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error="QUALITY_GATE WorkItem 需要 ArtifactRepository",
            )
        try:
            candidate = self._artifacts.candidate_for_work_item(
                trace_id=state.plan.trace.trace_id,
                artifact_key=item.publish_target or "",
                work_item_id=item.candidate_from_work_item_id or "",
            )
            self._artifacts.promote_candidate(
                candidate.id, artifact_key=item.publish_target or ""
            )
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as error:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=f"架构质量门拒绝发布: {error}",
            )
        return NodeResult.completed(
            node_id=item.id,
            agent_id=item.agent_id,
            content=f"已通过质量门并发布 {item.publish_target}: {candidate.id}",
        )

    @staticmethod
    def _build_task(state: RunState, item: WorkItem) -> str:
        return build_task_input(state, item).as_prompt()

    def _record_result(
        self, plan: ExecutionPlan, item: WorkItem, result: NodeResult
    ) -> None:
        if self._memory is not None:
            summary = result.content or result.error or result.status.value
            self._memory.append(
                trace_id=plan.trace.trace_id,
                role="control",
                event_type="work_item_result",
                content=summary,
                work_item_id=item.id,
                agent_id=item.agent_id,
                metadata={"status": result.status.value},
            )
        if result.status is NodeStatus.COMPLETED:
            self._record_event(plan, item, "work_item_completed")
            definition = self._agents.definition(item.agent_id)
            if (
                self._traces is not None
                and definition is not None
                and definition.domain == "requirement"
                and result.content is not None
            ):
                self._traces.snapshot_requirement(plan.trace, result.content)
            return
        if result.status is NodeStatus.NEEDS_CAPABILITY:
            self._record_event(plan, item, "work_item_waiting_capability")
            return
        if result.status is NodeStatus.NEEDS_REPLAN:
            signal = result.failure_signal
            self._record_event(
                plan,
                item,
                "work_item_needs_replan",
                details={
                    "kind": signal.kind.value if signal is not None else "unknown",
                    "evidence_id": signal.evidence_id if signal is not None else None,
                },
            )
            return
        self._record_event(
            plan,
            item,
            "work_item_failed",
            details={"error": result.error or "unknown"},
        )

    def _record_event(
        self,
        plan: ExecutionPlan,
        item: WorkItem,
        event_type: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._traces is not None:
            self._traces.record_event(
                plan.trace, item.id, event_type, details=details
            )

    def _finish_trace(self, plan: ExecutionPlan, result: GraphRunResult) -> None:
        if self._traces is not None:
            self._traces.finish_trace(
                plan.trace,
                result.status.value,
                error=result.error,
            )
        if self._memory is not None:
            self._memory.summarize_trace(
                plan.trace.trace_id,
                status=result.status.value,
                error=result.error,
            )

    def _handle_capability_request(
        self, state: RunState, result: NodeResult
    ) -> GraphRunResult:
        request = result.capability_request
        if request is None:
            return GraphRunResult(
                status=GraphRunStatus.FAILED,
                state=state,
                node_result=NodeResult.failed(
                    node_id=result.node_id,
                    agent_id=result.agent_id,
                    error="节点请求能力升级，但未提供能力请求详情",
                ),
                error="节点请求能力升级，但未提供能力请求详情",
            )

        definition = self._agents.definition(result.agent_id)
        if definition is None:
            return GraphRunResult(
                status=GraphRunStatus.FAILED,
                state=state,
                node_result=result,
                error=f"节点 Agent '{result.agent_id}' 未注册",
            )

        sources = self._tools.find_sources_for_capability(
            definition.domain, request.capability
        )
        if not sources:
            return GraphRunResult(
                status=GraphRunStatus.BLOCKED,
                state=state,
                node_result=result,
                error=f"没有可提供能力 '{request.capability}' 的来源",
            )

        return GraphRunResult(
            status=GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL,
            state=state,
            node_result=result,
            candidate_sources=tuple(
                SourceCandidate(
                    name=source.source_name,
                    capability=source.capability,
                )
                for source in sources
            ),
        )

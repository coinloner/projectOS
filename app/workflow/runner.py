from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.registry import AgentRegistry
from app.tool_manager.gateway import ToolGateway
from app.workflow.node_result import NodeResult, NodeStatus
from app.workflow.plan import ExecutionPlan
from app.workflow.state import RunState
from app.workflow.trace import TraceStore
from app.workflow.work_item import WorkItem


class GraphRunStatus(str, Enum):
    COMPLETED = "completed"
    WAITING_FOR_CAPABILITY_APPROVAL = "waiting_for_capability_approval"
    BLOCKED = "blocked"
    FAILED = "failed"


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
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._traces = traces

    def run(self, plan: ExecutionPlan) -> GraphRunResult:
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

            for item in ready_items:
                self._record_event(plan, item, "work_item_started")
                result = self._run_item(state, item)
                state.record(item, result)
                self._record_result(plan, item, result)

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

        graph_result = GraphRunResult(status=GraphRunStatus.COMPLETED, state=state)
        self._finish_trace(plan, graph_result)
        return graph_result

    def _run_item(self, state: RunState, item: WorkItem) -> NodeResult:
        definition = self._agents.definition(item.agent_id)
        if definition is None:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=f"ExecutionPlan 引用了未注册 Agent: '{item.agent_id}'",
            )

        try:
            agent_result = self._agents.create(item.agent_id).run(
                self._build_task(state, item)
            )
        except Exception as error:
            return NodeResult.failed(
                node_id=item.id,
                agent_id=item.agent_id,
                error=f"工作项 '{item.id}' 执行失败: {error}",
            )

        return NodeResult.from_agent_result(
            node_id=item.id,
            agent_id=item.agent_id,
            result=agent_result,
        )

    @staticmethod
    def _build_task(state: RunState, item: WorkItem) -> str:
        parts = [
            f"总体目标：{state.plan.goal}",
            f"当前工作项：{item.id}",
            f"当前任务：{item.objective}",
        ]
        if item.acceptance_criteria:
            parts.append("完成标准：")
            parts.extend(f"- {criterion}" for criterion in item.acceptance_criteria)
        if item.dependencies:
            parts.append("可用前置产物（按需调用 load_artifact 读取正文）：")
            for dependency in item.dependencies:
                dependency_item = state.plan.work_item(dependency.work_item_id)
                if dependency_item is None:
                    continue
                if dependency_item.output_key in state.artifacts:
                    parts.append(f"- {dependency_item.output_key}")
        return "\n\n".join(parts)

    def _record_result(
        self, plan: ExecutionPlan, item: WorkItem, result: NodeResult
    ) -> None:
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

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.registry import AgentRegistry
from app.tool_manager.gateway import ToolGateway
from app.workflow.node_result import NodeResult, NodeStatus
from app.workflow.plan import ExecutionPlan, TaskNode
from app.workflow.state import RunState


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

    def __init__(self, agents: AgentRegistry, tools: ToolGateway) -> None:
        self._agents = agents
        self._tools = tools

    def run(self, plan: ExecutionPlan) -> GraphRunResult:
        state = RunState(plan=plan)

        while not state.is_complete():
            ready_nodes = state.ready_nodes()
            if not ready_nodes:
                return GraphRunResult(
                    status=GraphRunStatus.FAILED,
                    state=state,
                    error="没有可执行节点，计划依赖未能推进",
                )

            for node in ready_nodes:
                result = self._run_node(state, node)
                state.record(node, result)

                if result.status is NodeStatus.FAILED:
                    return GraphRunResult(
                        status=GraphRunStatus.FAILED,
                        state=state,
                        node_result=result,
                        error=result.error,
                    )
                if result.status is NodeStatus.NEEDS_CAPABILITY:
                    return self._handle_capability_request(state, result)

        return GraphRunResult(status=GraphRunStatus.COMPLETED, state=state)

    def _run_node(self, state: RunState, node: TaskNode) -> NodeResult:
        definition = self._agents.definition(node.agent_id)
        if definition is None:
            return NodeResult.failed(
                node_id=node.id,
                agent_id=node.agent_id,
                error=f"ExecutionPlan 引用了未注册 Agent: '{node.agent_id}'",
            )

        try:
            agent_result = self._agents.create(node.agent_id).run(
                self._build_task(state, node)
            )
        except Exception as error:
            return NodeResult.failed(
                node_id=node.id,
                agent_id=node.agent_id,
                error=f"节点 '{node.id}' 执行失败: {error}",
            )

        return NodeResult.from_agent_result(
            node_id=node.id,
            agent_id=node.agent_id,
            result=agent_result,
        )

    @staticmethod
    def _build_task(state: RunState, node: TaskNode) -> str:
        parts = [
            f"总体目标：{state.plan.goal}",
            f"当前任务：{node.objective}",
        ]
        if node.depends_on:
            parts.append("可用前置产物（按需调用 load_artifact 读取正文）：")
            for dependency in node.depends_on:
                dependency_node = state.plan.node(dependency)
                if dependency_node is None:
                    continue
                if dependency_node.output_key in state.artifacts:
                    parts.append(f"- {dependency_node.output_key}")
        return "\n\n".join(parts)

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

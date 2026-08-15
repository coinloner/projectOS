from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.result import AgentResult, AgentStatus, CapabilityRequest


class NodeStatus(str, Enum):
    """Graph 中一次节点执行的结果状态。"""

    COMPLETED = "completed"
    NEEDS_CAPABILITY = "needs_capability"
    FAILED = "failed"


@dataclass(frozen=True)
class NodeResult:
    """节点执行结果，是 Graph 层与 Agent 层之间的标准边界。

    AgentResult 只说明 Agent 的回答；NodeResult 还说明是计划中的哪个节点、
    哪个已注册 Agent 产生了该结果。后续 GraphRunner 只处理 NodeResult。
    """

    node_id: str
    agent_id: str
    status: NodeStatus
    content: str | None = None
    capability_request: CapabilityRequest | None = None
    error: str | None = None

    @classmethod
    def from_agent_result(
        cls,
        *,
        node_id: str,
        agent_id: str,
        result: AgentResult,
    ) -> NodeResult:
        if result.status is AgentStatus.COMPLETED:
            return cls(
                node_id=node_id,
                agent_id=agent_id,
                status=NodeStatus.COMPLETED,
                content=result.content,
            )
        if result.status is AgentStatus.NEEDS_CAPABILITY:
            return cls(
                node_id=node_id,
                agent_id=agent_id,
                status=NodeStatus.NEEDS_CAPABILITY,
                capability_request=result.capability_request,
            )
        return cls.failed(
            node_id=node_id,
            agent_id=agent_id,
            error=f"不支持的 Agent 状态: {result.status}",
        )

    @classmethod
    def failed(cls, *, node_id: str, agent_id: str, error: str) -> NodeResult:
        return cls(
            node_id=node_id,
            agent_id=agent_id,
            status=NodeStatus.FAILED,
            error=error,
        )

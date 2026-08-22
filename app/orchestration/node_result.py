"""Agent 结果到编排层结果的边界模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.result import AgentResult, AgentStatus, CapabilityRequest
from app.orchestration.retry import FailureSignal


class NodeStatus(str, Enum):
    """Graph 中一次节点执行的结果状态。"""

    COMPLETED = "completed"
    NEEDS_CAPABILITY = "needs_capability"
    NEEDS_REPLAN = "needs_replan"
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
    failure_signal: FailureSignal | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "content": self.content,
            "capability_request": (
                {
                    "capability": self.capability_request.capability,
                    "reason": self.capability_request.reason,
                }
                if self.capability_request is not None
                else None
            ),
            "failure_signal": (
                self.failure_signal.as_dict()
                if self.failure_signal is not None
                else None
            ),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "NodeResult":
        capability_payload = payload.get("capability_request")
        capability = (
            CapabilityRequest(
                capability=str(capability_payload["capability"]),
                reason=str(capability_payload["reason"]),
            )
            if isinstance(capability_payload, dict)
            else None
        )
        failure_payload = payload.get("failure_signal")
        failure = (
            FailureSignal.from_dict(failure_payload)
            if isinstance(failure_payload, dict)
            else None
        )
        return cls(
            node_id=str(payload["node_id"]),
            agent_id=str(payload["agent_id"]),
            status=NodeStatus(str(payload["status"])),
            content=(
                str(payload["content"])
                if payload.get("content") is not None
                else None
            ),
            capability_request=capability,
            failure_signal=failure,
            error=(
                str(payload["error"])
                if payload.get("error") is not None
                else None
            ),
        )

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

    @classmethod
    def completed(cls, *, node_id: str, agent_id: str, content: str) -> NodeResult:
        return cls(
            node_id=node_id,
            agent_id=agent_id,
            status=NodeStatus.COMPLETED,
            content=content,
        )

    @classmethod
    def needs_replan(
        cls,
        *,
        node_id: str,
        agent_id: str,
        signal: FailureSignal,
        content: str | None = None,
    ) -> NodeResult:
        return cls(
            node_id=node_id,
            agent_id=agent_id,
            status=NodeStatus.NEEDS_REPLAN,
            content=content,
            failure_signal=signal,
        )

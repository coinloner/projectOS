from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_CAPABILITY = "needs_capability"


@dataclass(frozen=True)
class CapabilityRequest:
    """Agent 无法用当前工具完成任务时提出的能力缺口。"""

    capability: str
    reason: str


@dataclass(frozen=True)
class AgentResult:
    """Agent 单次运行的结构化结果。

    Workflow 将来根据 status 决定结束任务，或为 capability_request 授权额外来源。
    """

    status: AgentStatus
    content: str | None = None
    capability_request: CapabilityRequest | None = None

    @classmethod
    def completed(cls, content: str) -> AgentResult:
        return cls(status=AgentStatus.COMPLETED, content=content)

    @classmethod
    def needs_capability(
        cls, capability: str, reason: str
    ) -> AgentResult:
        return cls(
            status=AgentStatus.NEEDS_CAPABILITY,
            capability_request=CapabilityRequest(capability, reason),
        )

    def __str__(self) -> str:
        """让现有 CLI 输出在迁移期间仍可直接打印结果。"""
        if self.content is not None:
            return self.content
        if self.capability_request is not None:
            return self.capability_request.reason
        return ""


def from_llm_content(content: str) -> AgentResult:
    """仅识别严格的能力请求 JSON；其他文本一律视为正常完成。"""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return AgentResult.completed(content)

    if (
        isinstance(payload, dict)
        and payload.get("type") == "capability_request"
        and isinstance(payload.get("capability"), str)
        and payload["capability"].strip()
        and isinstance(payload.get("reason"), str)
        and payload["reason"].strip()
    ):
        return AgentResult.needs_capability(
            payload["capability"].strip(), payload["reason"].strip()
        )

    return AgentResult.completed(content)

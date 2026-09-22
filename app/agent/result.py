from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_CAPABILITY = "needs_capability"


@dataclass(frozen=True)
class CapabilityRequest:
    """Agent 无法用当前工具完成任务时提出的能力缺口。"""

    capability: str
    reason: str

    def normalized(self) -> "CapabilityRequest":
        """Return a canonical single capability id at the Agent boundary."""
        return CapabilityRequest(normalize_capability(self.capability), self.reason.strip())


def normalize_capability(value: str) -> str:
    """Normalize model wording without turning a multi-capability list into an id."""
    raw = str(value or "").strip().lower()
    parts = [part.strip() for part in re.split(r"[,，、;；|\s]+", raw) if part.strip()]
    # A model may combine an always-local input reader with the only genuine
    # missing capability (for example ``load_code_input_and_external_documentation``).
    # Local tools are not grantable capabilities; reduce the request to the
    # canonical external-documentation id so an existing Trace grant can be
    # reused and the MCP source can be exposed on retry.
    if "external" in raw and ("document" in raw or "doc" in raw):
        return "external_documentation"
    if set(parts) >= {"prepare_environment", "save_environment"}:
        return "environment_preparation"
    # Models sometimes collapse the two local environment steps and the
    # dependency approval into one descriptive identifier.  That is still the
    # same control-plane capability: once dependencies are approved and the
    # environment is ready, the checkpoint can resume without an external
    # source.  Keep this normalization at the Agent boundary so API and runner
    # see one canonical value.
    if (
        "environment" in raw
        and ("save" in raw or "prepare" in raw or "persistence" in raw)
        and ("approval" in raw or "dependency" in raw or "persistence" in raw)
    ):
        return "environment_preparation"
    aliases = {
        "save environment": "save_environment",
        "prepare environment": "prepare_environment",
        "external documentation": "external_documentation",
    }
    return aliases.get(raw, raw)


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
        return cls(status=AgentStatus.NEEDS_CAPABILITY,
                   capability_request=CapabilityRequest(capability, reason).normalized())

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

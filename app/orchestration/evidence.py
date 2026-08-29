"""由受控执行器产生的结构化运行证据。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.execution_context import ExecutionContext
from app.sandbox.result import SandboxResult, SandboxStatus
from uuid import uuid4


@dataclass(frozen=True)
class SandboxEvidence:
    """一次 Docker 检查的不可由 LLM 伪造的原始结果。"""

    id: str
    trace_id: str
    work_item_id: str
    agent_id: str
    check_id: str
    runtime_profile: str | None
    status: SandboxStatus
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    created_at: str
    message: str | None = None

    @classmethod
    def from_sandbox_result(
        cls,
        *,
        evidence_id: str,
        context: ExecutionContext,
        result: SandboxResult,
        created_at: str,
    ) -> "SandboxEvidence":
        return cls(
            id=evidence_id,
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            agent_id=context.agent_id,
            check_id=result.check_id,
            runtime_profile=result.runtime_profile,
            status=result.status,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            stdout=result.stdout,
            stderr=result.stderr,
            created_at=created_at,
            message=result.message,
        )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload

    def as_agent_text(self) -> str:
        """返回给当前 Agent 的可读摘要，完整原始结果已独立持久化。"""
        parts = [
            f"evidence_id={self.id}",
            f"status={self.status.value}",
            f"check_id={self.check_id}",
            f"exit_code={self.exit_code}",
            f"duration_ms={self.duration_ms}",
        ]
        if self.runtime_profile:
            parts.append(f"runtime_profile={self.runtime_profile}")
        if self.message:
            parts.append(f"message={self.message}")
        if self.stdout:
            parts.append("stdout:\n" + self.stdout)
        if self.stderr:
            parts.append("stderr:\n" + self.stderr)
        return "\n".join(parts)


@dataclass(frozen=True)
class RuntimeEvidence:
    """控制面阶段证据，统一记录环境、构建、迁移、启动和健康检查。"""

    id: str
    trace_id: str
    phase: str
    status: str
    work_item_id: str | None = None
    exit_code: int | None = None
    duration_ms: int = 0
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    message: str | None = None

    @classmethod
    def create(cls, *, trace_id: str, phase: str, status: str, **kwargs: object) -> "RuntimeEvidence":
        return cls(id=f"rev-{uuid4().hex[:12]}", trace_id=trace_id, phase=phase, status=status, **kwargs)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["command"] = list(self.command)
        return payload

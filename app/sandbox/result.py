"""Sandbox 执行的结构化结果。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SandboxStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SETUP_FAILED = "setup_failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class SandboxResult:
    status: SandboxStatus
    check_id: str
    runtime_profile: str | None
    exit_code: int | None
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    message: str | None = None

    def as_agent_text(self) -> str:
        parts = [
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

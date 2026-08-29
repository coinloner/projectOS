"""跨工具运行时传递的可信执行身份。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.artifact.repository import ArtifactRef

if TYPE_CHECKING:
    from app.memory.store import MemoryStore
    from app.llm.config import LLMSelection


class ExecutionMode(str, Enum):
    """工作项由系统分配的产物写入角色。"""

    EXCLUSIVE = "exclusive"
    PARTITIONED = "partitioned"
    INTEGRATION = "integration"
    QUALITY_GATE = "quality_gate"


@dataclass(frozen=True)
class ExecutionContext:
    """标识一次受 GraphRunner 调度的工具调用归属。

    该对象由编排层创建并通过 ToolGateway 绑定到工具对象。它不是 LLM 的任务
    文本，也不属于工具参数 schema，因此模型不能伪造或修改其中的身份字段。
    """

    trace_id: str
    work_item_id: str
    agent_id: str
    execution_mode: ExecutionMode = ExecutionMode.EXCLUSIVE
    input_refs: tuple[ArtifactRef, ...] = ()
    output_slot: str | None = None
    publish_target: str | None = None
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    implementation_unit_id: str | None = None
    memory: "MemoryStore | None" = None
    progress: Any | None = None
    llm_selection: "LLMSelection | None" = None
    owned_files: tuple[str, ...] = ()
    # Optional per-attempt narrowing used by the control plane for bounded
    # retries.  Empty means the normal execution-mode tool set.
    tool_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("trace_id", "work_item_id", "agent_id"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"ExecutionContext.{field_name} 不能为空")
        if self.execution_mode is ExecutionMode.PARTITIONED and not self.output_slot:
            raise ValueError("分区执行必须包含 output_slot")
        if self.execution_mode is ExecutionMode.INTEGRATION and not self.publish_target:
            raise ValueError("集成执行必须包含 publish_target")

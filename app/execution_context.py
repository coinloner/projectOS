"""跨工具运行时传递的可信执行身份。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionContext:
    """标识一次受 GraphRunner 调度的工具调用归属。

    该对象由编排层创建并通过 ToolGateway 绑定到工具对象。它不是 LLM 的任务
    文本，也不属于工具参数 schema，因此模型不能伪造或修改其中的身份字段。
    """

    trace_id: str
    work_item_id: str
    agent_id: str

    def __post_init__(self) -> None:
        for field_name in ("trace_id", "work_item_id", "agent_id"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"ExecutionContext.{field_name} 不能为空")

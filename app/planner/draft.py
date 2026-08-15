"""LLM 输出的、尚未可信的计划草案模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PlannedStep(BaseModel):
    """Planner 只能选择 Agent、描述目标并声明 Agent 级依赖。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list, max_length=10)


class PlanDraft(BaseModel):
    """LLM 计划输出的窄 schema，不包含 node id、output key 或工具。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1000)
    steps: list[PlannedStep] = Field(min_length=1, max_length=10)
    template_hint_id: str | None = Field(default=None, max_length=100)

    @classmethod
    def parse(cls, content: str) -> PlanDraft:
        try:
            return cls.model_validate_json(content)
        except ValidationError as error:
            raise PlanDraftError(f"计划草案不符合 JSON schema: {error}") from error


class PlanDraftError(ValueError):
    """Planner 输出无法解析为 PlanDraft。"""

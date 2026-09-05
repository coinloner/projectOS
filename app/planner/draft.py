"""LLM 输出的、尚未可信的计划草案模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.json_transport import strip_json_transport_noise


class PlannedStep(BaseModel):
    """Planner 选择 Agent，并用临时 ref 描述本次工作项图。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=500)
    # Process stage is a semantic role only; execution mode and permissions
    # are still compiled by PlanValidator from the registered process.
    stage_id: str | None = Field(default=None, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=10)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=5)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    non_goals: list[str] = Field(default_factory=list, max_length=8)


class TemplateDependencyOverride(BaseModel):
    """对默认模板依赖的显式移除说明。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    predecessor_agent_id: str = Field(min_length=1, max_length=100)
    successor_agent_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class PlanDraft(BaseModel):
    """LLM 计划输出的窄 schema，不包含 node id、output key 或工具。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1000)
    steps: list[PlannedStep] = Field(min_length=0, max_length=10)
    template_hint_id: str | None = Field(default=None, max_length=100)
    process_id: str | None = Field(default=None, max_length=100)
    template_dependency_overrides: list[TemplateDependencyOverride] = Field(
        default_factory=list, max_length=10
    )

    @classmethod
    def parse(cls, content: str) -> PlanDraft:
        normalized = strip_json_transport_noise(content)
        try:
            return cls.model_validate_json(normalized)
        except ValidationError as error:
            raise PlanDraftError(f"计划草案不符合 JSON schema: {error}") from error


class PlanDraftError(ValueError):
    """Planner 输出无法解析为 PlanDraft。"""

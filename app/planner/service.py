"""Planner v0 服务：构造上下文、请求草案、校验并执行一次修复。"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.registry import AgentRegistry
from app.artifact.store import ArtifactStore
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft, PlanDraftError
from app.planner.errors import PlanValidationError
from app.planner.planner import PlannerRuntime
from app.planner.validator import PlanValidator
from app.workflow.plan import ExecutionPlan
from app.workflow.template import WorkflowTemplateRegistry
from app.workflow.trace import TraceStore


@dataclass(frozen=True)
class PlannerResult:
    """一次成功规划的可展示结果。"""

    plan: ExecutionPlan
    draft: PlanDraft
    context: PlanningContext
    attempts: int


class PlannerFailure(RuntimeError):
    """Planner 在一次修复后仍无法产出合法计划。"""


class PlannerService:
    """ProjectOS 的计划入口，不向 Planner 暴露可执行对象。"""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        agents: AgentRegistry,
        templates: WorkflowTemplateRegistry,
        artifacts: ArtifactStore,
        validator: PlanValidator | None = None,
        traces: TraceStore | None = None,
    ) -> None:
        self._runtime = runtime
        self._agents = agents
        self._templates = templates
        self._artifacts = artifacts
        self._validator = validator or PlanValidator()
        self._traces = traces or TraceStore(artifacts.project_path)

    def plan(self, *, goal: str, plan_id: str) -> PlannerResult:
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        prompt = _planning_prompt(context)
        trace = self._traces.start_trace(goal.strip())

        for attempt in (1, 2):
            raw_draft = self._runtime.generate(prompt)
            try:
                draft = PlanDraft.parse(raw_draft)
                plan = self._validator.validate(
                    draft,
                    context=context,
                    plan_id=plan_id,
                    trace=trace,
                )
                return PlannerResult(
                    plan=plan,
                    draft=draft,
                    context=context,
                    attempts=attempt,
                )
            except (PlanDraftError, PlanValidationError) as error:
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在修复后仍无法生成合法计划: {error}"
                    ) from error
                prompt = _repair_prompt(context, raw_draft, str(error))

        raise AssertionError("Planner 修复循环未按预期结束")


def _planning_prompt(context: PlanningContext) -> str:
    return (
        "请为以下 ProjectOS 上下文生成 PlanDraft JSON。\n\n"
        "上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        "只输出 PlanDraft JSON，不要输出 Markdown 或解释。"
    )


def _repair_prompt(context: PlanningContext, invalid_draft: str, error: str) -> str:
    return (
        "上一份 PlanDraft 无法执行。请保留原目标并仅输出一份修正后的 PlanDraft JSON。\n\n"
        f"规划上下文：\n{context.as_prompt_json()}\n\n"
        f"上一份草案：\n{invalid_draft}\n\n"
        f"校验错误：\n{error}\n\n"
        "不要输出 Markdown 或解释。"
    )

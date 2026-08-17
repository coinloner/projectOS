"""PlanDraft 到含来源依赖的 WorkItem ExecutionPlan 转换。"""

from __future__ import annotations

from app.planner.context import PlanningContext
from app.planner.dependency_policy import DependencyPolicy
from app.planner.draft import PlanDraft
from app.planner.errors import PlanValidationError
from app.workflow.plan import ExecutionPlan
from app.workflow.trace import TraceContext
from app.workflow.work_item import WorkItem


class PlanValidator:
    """只允许 Planner 在已注册 Agent 合同范围内组装 WorkItem 图。"""

    def __init__(self, dependency_policy: DependencyPolicy | None = None) -> None:
        self._dependency_policy = dependency_policy or DependencyPolicy()

    def validate(
        self,
        draft: PlanDraft,
        *,
        context: PlanningContext,
        plan_id: str,
        trace: TraceContext | None = None,
    ) -> ExecutionPlan:
        known_agents = {agent.id: agent for agent in context.agents}
        selected_ids = [step.agent_id for step in draft.steps]
        self._validate_unique(selected_ids, "Planner v0 不允许同一 Agent 重复出现")
        self._validate_unique([step.ref for step in draft.steps], "PlanDraft 包含重复 step ref")

        unknown_agents = sorted(set(selected_ids) - set(known_agents))
        if unknown_agents:
            raise PlanValidationError(
                f"计划引用未注册 Agent: {', '.join(unknown_agents)}"
            )
        if draft.template_hint_id is not None:
            known_templates = {template.id for template in context.templates}
            if draft.template_hint_id not in known_templates:
                raise PlanValidationError(
                    f"计划引用未知模板: '{draft.template_hint_id}'"
                )

        step_refs = {step.ref for step in draft.steps}
        for step in draft.steps:
            self._validate_step_dependencies(step.ref, step.depends_on, step_refs)

        item_id_by_ref = {
            step.ref: self._work_item_id(index, known_agents[step.agent_id].output_key)
            for index, step in enumerate(draft.steps, 1)
        }
        item_id_by_agent = {
            step.agent_id: item_id_by_ref[step.ref] for step in draft.steps
        }
        dependencies = self._dependency_policy.resolve(
            draft=draft,
            context=context,
            item_id_by_agent=item_id_by_agent,
            item_id_by_ref=item_id_by_ref,
        )
        work_items = tuple(
            WorkItem(
                id=item_id_by_ref[step.ref],
                agent_id=step.agent_id,
                objective=step.objective,
                output_key=known_agents[step.agent_id].output_key,
                dependencies=dependencies[item_id_by_ref[step.ref]],
                acceptance_criteria=tuple(step.acceptance_criteria),
            )
            for step in draft.steps
        )
        try:
            return ExecutionPlan(
                id=plan_id,
                goal=context.goal,
                work_items=work_items,
                template_id=draft.template_hint_id,
                trace=trace or TraceContext.ephemeral(),
            )
        except ValueError as error:
            raise PlanValidationError(f"计划不是有效 DAG: {error}") from error

    @staticmethod
    def _work_item_id(index: int, output_key: str) -> str:
        return f"wi-{index:02d}-{output_key}"

    @staticmethod
    def _validate_unique(values: list[str], message: str) -> None:
        duplicates = _duplicates(values)
        if duplicates:
            raise PlanValidationError(message + f": {', '.join(duplicates)}")

    @staticmethod
    def _validate_step_dependencies(
        step_ref: str, dependencies: list[str], known_refs: set[str]
    ) -> None:
        duplicates = _duplicates(dependencies)
        if duplicates:
            raise PlanValidationError(
                f"步骤 '{step_ref}' 包含重复依赖: {', '.join(duplicates)}"
            )
        if step_ref in dependencies:
            raise PlanValidationError(f"步骤 '{step_ref}' 不能依赖自身")
        unknown = sorted(set(dependencies) - known_refs)
        if unknown:
            raise PlanValidationError(
                f"步骤 '{step_ref}' 依赖未选择的 step ref: {', '.join(unknown)}"
            )


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates

"""PlanDraft 到 ExecutionPlan 的受控转换。"""

from __future__ import annotations

from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft
from app.workflow.plan import ExecutionPlan, TaskNode


class PlanValidationError(ValueError):
    """草案违反 ProjectOS 规划约束。"""


class PlanValidator:
    """只允许 Planner 在已注册 Agent 合同范围内组装 DAG。"""

    def validate(
        self,
        draft: PlanDraft,
        *,
        context: PlanningContext,
        plan_id: str,
    ) -> ExecutionPlan:
        known_agents = {agent.id: agent for agent in context.agents}
        selected_ids = [step.agent_id for step in draft.steps]

        duplicate_ids = _duplicates(selected_ids)
        if duplicate_ids:
            raise PlanValidationError(
                f"Planner v0 不允许同一 Agent 重复出现: {', '.join(duplicate_ids)}"
            )

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

        selected = set(selected_ids)
        self._validate_delivery_evidence(draft, context, selected)
        nodes: list[TaskNode] = []
        for step in draft.steps:
            duplicate_dependencies = _duplicates(step.depends_on)
            if duplicate_dependencies:
                raise PlanValidationError(
                    f"Agent '{step.agent_id}' 包含重复依赖: "
                    f"{', '.join(duplicate_dependencies)}"
                )
            if step.agent_id in step.depends_on:
                raise PlanValidationError(
                    f"Agent '{step.agent_id}' 不能依赖自身"
                )
            unknown_dependencies = sorted(set(step.depends_on) - selected)
            if unknown_dependencies:
                raise PlanValidationError(
                    f"Agent '{step.agent_id}' 依赖未选择的 Agent: "
                    f"{', '.join(unknown_dependencies)}"
                )

            definition = known_agents[step.agent_id]
            nodes.append(
                TaskNode(
                    id=definition.output_key,
                    agent_id=definition.id,
                    objective=step.objective,
                    output_key=definition.output_key,
                    depends_on=tuple(
                        known_agents[dependency].output_key
                        for dependency in step.depends_on
                    ),
                )
            )

        try:
            return ExecutionPlan(
                id=plan_id,
                goal=context.goal,
                nodes=tuple(nodes),
                template_id=draft.template_hint_id,
            )
        except ValueError as error:
            raise PlanValidationError(f"计划不是有效 DAG: {error}") from error

    @staticmethod
    def _validate_delivery_evidence(
        draft: PlanDraft,
        context: PlanningContext,
        selected: set[str],
    ) -> None:
        """防止实现摘要被误当成真实代码交付。"""
        if context.workspace.implementation_file_count > 0:
            return

        steps = {step.agent_id: step for step in draft.steps}
        if "test_agent" in selected and "code_agent" not in selected:
            raise PlanValidationError(
                "workspace 尚无实现文件，选择 test_agent 前必须选择 code_agent"
            )
        if "review_agent" in selected:
            missing = {"code_agent", "test_agent"} - selected
            if missing:
                raise PlanValidationError(
                    "workspace 尚无实现文件，选择 review_agent 前必须先选择: "
                    + ", ".join(sorted(missing))
                )
        if "test_agent" in selected and "code_agent" not in steps["test_agent"].depends_on:
            raise PlanValidationError("test_agent 必须依赖 code_agent")
        if "review_agent" in selected:
            required = {"code_agent", "test_agent"}
            actual = set(steps["review_agent"].depends_on)
            if not required <= actual:
                raise PlanValidationError(
                    "review_agent 必须依赖 code_agent 和 test_agent"
                )

        if not context.runtime.manifest_exists and (
            "code_agent" in selected or "test_agent" in selected
        ):
            if "bootstrap_agent" not in selected:
                raise PlanValidationError(
                    "runtime 未声明，选择 code_agent 或 test_agent 前必须选择 bootstrap_agent"
                )
            bootstrap_dependencies = set(steps["bootstrap_agent"].depends_on)
            if "code_agent" in steps and "bootstrap_agent" not in steps["code_agent"].depends_on:
                raise PlanValidationError("code_agent 必须依赖 bootstrap_agent")
            if "test_agent" in steps and "bootstrap_agent" not in steps["test_agent"].depends_on:
                raise PlanValidationError("test_agent 必须依赖 bootstrap_agent")


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates

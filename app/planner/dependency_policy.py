"""将系统规则、模板经验与 Planner 选择合并为 WorkItem 依赖。"""

from __future__ import annotations

from app.planner.context import PlanningContext, TemplateHint
from app.planner.draft import PlanDraft
from app.planner.errors import PlanValidationError
from app.workflow.work_item import DependencySource, WorkItemDependency


class DependencyPolicy:
    """系统依赖不可移除；模板依赖可经明确 override 移除。"""

    def resolve(
        self,
        *,
        draft: PlanDraft,
        context: PlanningContext,
        item_id_by_agent: dict[str, str],
        item_id_by_ref: dict[str, str],
    ) -> dict[str, tuple[WorkItemDependency, ...]]:
        dependencies: dict[str, dict[str, WorkItemDependency]] = {
            item_id: {} for item_id in item_id_by_agent.values()
        }

        for step in draft.steps:
            target = item_id_by_ref[step.ref]
            for dependency_ref in step.depends_on:
                self._add(
                    dependencies,
                    target,
                    WorkItemDependency(
                        work_item_id=item_id_by_ref[dependency_ref],
                        source=DependencySource.PLANNER,
                        rule_id=f"planner:{dependency_ref}->{step.ref}",
                    ),
                )

        template = self._template_for(draft, context)
        if template is not None:
            overrides = {
                (override.predecessor_agent_id, override.successor_agent_id)
                for override in draft.template_dependency_overrides
            }
            self._validate_overrides(overrides, template, item_id_by_agent)
            for node in template.nodes:
                if node.agent_id not in item_id_by_agent:
                    continue
                for predecessor_agent_id in node.depends_on:
                    if predecessor_agent_id not in item_id_by_agent:
                        continue
                    edge = (predecessor_agent_id, node.agent_id)
                    if edge in overrides:
                        continue
                    self._add(
                        dependencies,
                        item_id_by_agent[node.agent_id],
                        WorkItemDependency(
                            work_item_id=item_id_by_agent[predecessor_agent_id],
                            source=DependencySource.TEMPLATE,
                            rule_id=(
                                f"template:{template.id}:"
                                f"{predecessor_agent_id}->{node.agent_id}"
                            ),
                        ),
                    )

        self._apply_system_rules(
            dependencies=dependencies,
            context=context,
            selected_agents=set(item_id_by_agent),
            item_id_by_agent=item_id_by_agent,
        )
        return {
            item_id: tuple(item_dependencies.values())
            for item_id, item_dependencies in dependencies.items()
        }

    @staticmethod
    def _template_for(
        draft: PlanDraft, context: PlanningContext
    ) -> TemplateHint | None:
        if draft.template_hint_id is None:
            return None
        return next(
            template
            for template in context.templates
            if template.id == draft.template_hint_id
        )

    @staticmethod
    def _validate_overrides(
        overrides: set[tuple[str, str]],
        template: TemplateHint,
        item_id_by_agent: dict[str, str],
    ) -> None:
        template_edges = {
            (predecessor, node.agent_id)
            for node in template.nodes
            for predecessor in node.depends_on
        }
        for edge in overrides:
            if edge not in template_edges:
                raise PlanValidationError(
                    "模板依赖 override 不存在: " + " -> ".join(edge)
                )
            if not set(edge) <= set(item_id_by_agent):
                raise PlanValidationError(
                    "模板依赖 override 只能引用已选择 Agent: "
                    + " -> ".join(edge)
                )

    def _apply_system_rules(
        self,
        *,
        dependencies: dict[str, dict[str, WorkItemDependency]],
        context: PlanningContext,
        selected_agents: set[str],
        item_id_by_agent: dict[str, str],
    ) -> None:
        if context.workspace.implementation_file_count == 0:
            if "test_agent" in selected_agents:
                self._require_agent(selected_agents, "code_agent", "test_agent")
                self._add_system_dependency(
                    dependencies, item_id_by_agent, "code_agent", "test_agent", "test_requires_code"
                )
            if "review_agent" in selected_agents:
                for predecessor in ("code_agent", "test_agent"):
                    self._require_agent(selected_agents, predecessor, "review_agent")
                    self._add_system_dependency(
                        dependencies,
                        item_id_by_agent,
                        predecessor,
                        "review_agent",
                        f"review_requires_{predecessor.removesuffix('_agent')}",
                    )

        if not context.runtime.manifest_exists:
            for successor in ("code_agent", "test_agent"):
                if successor not in selected_agents:
                    continue
                self._require_agent(selected_agents, "bootstrap_agent", successor)
                self._add_system_dependency(
                    dependencies,
                    item_id_by_agent,
                    "bootstrap_agent",
                    successor,
                    f"{successor.removesuffix('_agent')}_requires_bootstrap",
                )

    @staticmethod
    def _require_agent(
        selected_agents: set[str], predecessor: str, successor: str
    ) -> None:
        if predecessor not in selected_agents:
            raise PlanValidationError(
                f"选择 {successor} 前必须先选择 {predecessor}"
            )

    def _add_system_dependency(
        self,
        dependencies: dict[str, dict[str, WorkItemDependency]],
        item_id_by_agent: dict[str, str],
        predecessor_agent: str,
        successor_agent: str,
        rule_id: str,
    ) -> None:
        self._add(
            dependencies,
            item_id_by_agent[successor_agent],
            WorkItemDependency(
                work_item_id=item_id_by_agent[predecessor_agent],
                source=DependencySource.SYSTEM,
                rule_id=rule_id,
            ),
            overwrite=True,
        )

    @staticmethod
    def _add(
        dependencies: dict[str, dict[str, WorkItemDependency]],
        target: str,
        dependency: WorkItemDependency,
        *,
        overwrite: bool = False,
    ) -> None:
        if overwrite or dependency.work_item_id not in dependencies[target]:
            dependencies[target][dependency.work_item_id] = dependency

"""将带系统授权的 WorkflowTemplate 编译为一次真实 ExecutionPlan。"""

from __future__ import annotations

from collections.abc import Mapping

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.workflow.template import WorkflowTemplate


class TemplateCompiler:
    """展开受控模板，不接受来自 Planner 的权限字段。"""

    def compile(
        self,
        template: WorkflowTemplate,
        *,
        goal: str,
        plan_id: str,
        trace: TraceContext,
        agent_output_keys: Mapping[str, str] | None = None,
    ) -> ExecutionPlan:
        if not template.has_controlled_execution:
            raise ValueError(
                f"模板 '{template.id}' 是普通模板，应由 PlanValidator 的动态路径处理"
            )
        contracts = agent_output_keys or {}
        unknown_agents = sorted(
            {node.agent_id for node in template.nodes} - set(contracts)
        )
        if unknown_agents:
            raise ValueError(
                f"受控模板引用未注册 Agent: {', '.join(unknown_agents)}"
            )

        item_id_by_blueprint = {
            node.id: f"wi-{index:02d}-{node.id}"
            for index, node in enumerate(template.nodes, 1)
        }
        staged_refs = {
            node.id: ArtifactRef.staged(
                artifact_key=self._artifact_key(node),
                trace_id=trace.trace_id,
                work_item_id=item_id_by_blueprint[node.id],
                slot=node.output_slot or "",
            )
            for node in template.nodes
            if node.execution_mode is ExecutionMode.PARTITIONED
        }

        work_items: list[WorkItem] = []
        for node in template.nodes:
            item_id = item_id_by_blueprint[node.id]
            dependencies = tuple(
                WorkItemDependency(
                    work_item_id=item_id_by_blueprint[predecessor],
                    source=DependencySource.TEMPLATE,
                    rule_id=f"template:{template.id}:{predecessor}->{node.id}",
                )
                for predecessor in node.depends_on
            )
            input_refs = self._input_refs(
                node=node,
                trace=trace,
                item_id_by_blueprint=item_id_by_blueprint,
                staged_refs=staged_refs,
            )
            if node.candidate_from is not None and node.candidate_from not in item_id_by_blueprint:
                raise ValueError(
                    f"Blueprint '{node.id}' 的 candidate_from 引用了未知节点"
                )
            candidate_from = (
                item_id_by_blueprint[node.candidate_from]
                if node.candidate_from is not None
                else None
            )
            work_items.append(
                WorkItem(
                    id=item_id,
                    agent_id=node.agent_id,
                    objective=node.objective,
                    output_key=node.output_key,
                    artifact_key=self._artifact_key(node),
                    dependencies=dependencies,
                    acceptance_criteria=node.acceptance_criteria,
                    constraints=node.constraints,
                    non_goals=node.non_goals,
                    policy_id=node.policy_id,
                    execution_mode=node.execution_mode,
                    input_refs=input_refs,
                    output_slot=node.output_slot,
                    publish_target=node.publish_target,
                    candidate_from_work_item_id=candidate_from,
                )
            )

        return ExecutionPlan(
            id=plan_id,
            goal=goal,
            work_items=tuple(work_items),
            template_id=template.id,
            trace=trace,
        )

    @staticmethod
    def _artifact_key(node) -> str:
        return node.artifact_key or node.publish_target or node.output_key

    def _input_refs(
        self,
        *,
        node,
        trace: TraceContext,
        item_id_by_blueprint: Mapping[str, str],
        staged_refs: Mapping[str, ArtifactRef],
    ) -> tuple[ArtifactRef, ...]:
        refs = [ArtifactRef.published(artifact_key) for artifact_key in node.input_artifacts]
        for source_id in node.input_from:
            if source_id not in item_id_by_blueprint:
                raise ValueError(
                    f"Blueprint '{node.id}' 的 input_from 引用了未知节点 '{source_id}'"
                )
            source_ref = staged_refs.get(source_id)
            if source_ref is None:
                raise ValueError(
                    f"Blueprint '{node.id}' 只能从 PARTITIONED 节点读取 staged output: '{source_id}'"
                )
            refs.append(source_ref)
        return tuple(refs)

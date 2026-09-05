"""动态架构计划的下游交付阶段编译器。

Architecture 的模块数量和实现单元由设计对象决定；Tasks、Environment、Integration、Test
和 Review 则是软件交付流程中稳定的生命周期角色。这个编译器只在动态计划已经明确需要
代码验证/审查时追加这些角色，不预先猜测项目模块或文件数量。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.work_item import (
    DependencySource,
    WorkItem,
    WorkItemDependency,
)


@dataclass(frozen=True)
class DeliveryTailExpansion:
    """追加动态交付尾部后的计划及其节点身份。"""

    plan: ExecutionPlan
    work_item_ids: tuple[str, ...]


class DynamicDeliveryTailBuilder:
    """根据流程角色追加一次动态交付尾部。"""

    _PREFIX = "wi-dynamic-tail-"
    _REPLACED_AGENTS = frozenset(
        {
            "task_agent",
            "bootstrap_agent",
            "code_agent",
            "code_integration_agent",
            "test_agent",
            "review_agent",
        }
    )

    def append(
        self,
        plan: ExecutionPlan,
        *,
        contract_item_id: str,
        requirement_item_id: str | None = None,
        architecture_item_id: str | None = None,
    ) -> DeliveryTailExpansion:
        """追加 Tasks -> Environment -> Code -> Test -> Review 角色。

        ``contract_item_id`` 必须已经存在且代表已发布 Project Contract。所有新增节点
        使用固定生命周期权限，但它们的输入引用和依赖只指向当前计划中的合同/需求，
        不包含任何项目特定的模块或文件假设。
        """
        if plan.work_item(contract_item_id) is None:
            raise ValueError(f"动态交付尾部缺少合同节点: {contract_item_id}")
        if any(item.id.startswith(self._PREFIX) for item in plan.work_items):
            return DeliveryTailExpansion(plan, ())
        requirement = self._resolve_item(
            plan,
            requirement_item_id,
            lambda item: item.agent_id == "requirement_agent",
            required=False,
        )
        architecture = self._resolve_item(
            plan,
            architecture_item_id,
            lambda item: item.agent_id == "architecture_agent"
            and item.publish_target == "architecture",
            required=False,
        )
        contract = plan.work_item(contract_item_id)
        assert contract is not None

        requirement_ref = ArtifactRef.published("requirement")
        architecture_ref = ArtifactRef.published("architecture")
        contract_ref = ArtifactRef.published("architecture_contract")

        def dep(item_id: str, rule: str) -> WorkItemDependency:
            return WorkItemDependency(item_id, DependencySource.SYSTEM, rule)

        task_plan_id = self._PREFIX + "tasks-plan"
        task_integration_id = self._PREFIX + "tasks-integration"
        task_quality_id = self._PREFIX + "tasks-quality"
        environment_id = self._PREFIX + "environment"
        code_integration_id = self._PREFIX + "code-integration"
        test_id = self._PREFIX + "test"
        review_id = self._PREFIX + "review"

        task_dependencies = [dep(contract.id, "dynamic-tail:contract")]
        if requirement is not None:
            task_dependencies.insert(0, dep(requirement.id, "dynamic-tail:requirement"))
        if architecture is not None:
            task_dependencies.insert(-1, dep(architecture.id, "dynamic-tail:architecture"))

        task_plan = WorkItem(
            id=task_plan_id,
            agent_id="task_agent",
            stage_id="task",
            objective="根据已发布需求和 Project Contract 生成可验收的实施任务决策包。",
            output_key="dynamic_tasks_plan",
            artifact_key="tasks",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="plan",
            input_refs=(requirement_ref, architecture_ref, contract_ref),
            dependencies=tuple(task_dependencies),
            acceptance_criteria=(
                "只列当前需求支持的 MVP 任务，并为每项提供产出和可验证验收条件。",
                "不得修改 Project Contract 的层级、路径或接口 ownership。",
            ),
            constraints=("任务只能引用已发布的 Requirement 和 Project Contract。",),
            output_kind="TaskObject",
        )
        task_plan_ref = ArtifactRef.staged(
            artifact_key="tasks",
            trace_id=plan.trace.trace_id,
            work_item_id=task_plan_id,
            slot="plan",
        )
        task_integration = WorkItem(
            id=task_integration_id,
            agent_id="task_agent",
            stage_id="task",
            objective="整合动态任务决策包并创建 tasks 候选。",
            output_key="dynamic_tasks_candidate",
            artifact_key="tasks",
            execution_mode=ExecutionMode.INTEGRATION,
            publish_target="tasks",
            input_refs=(task_plan_ref,),
            dependencies=(dep(task_plan_id, "dynamic-tail:tasks"),),
            acceptance_criteria=("只整合已授权任务，不新增需求之外的功能。",),
            output_kind="TaskCandidate",
        )
        task_quality = WorkItem(
            id=task_quality_id,
            agent_id="task_agent",
            stage_id="task",
            objective="验证动态任务候选并发布 tasks.md。",
            output_key="dynamic_tasks_published",
            artifact_key="tasks",
            execution_mode=ExecutionMode.QUALITY_GATE,
            publish_target="tasks",
            candidate_from_work_item_id=task_integration_id,
            dependencies=(dep(task_integration_id, "dynamic-tail:tasks-quality"),),
            output_kind="TaskObject",
        )
        environment = WorkItem(
            id=environment_id,
            agent_id="bootstrap_agent",
            stage_id="environment",
            objective="根据 Project Contract 和任务声明准备受信运行环境。",
            output_key="dynamic_environment",
            artifact_key="environment",
            input_refs=(requirement_ref, architecture_ref, contract_ref, ArtifactRef.published("tasks")),
            dependencies=(
                dep(contract.id, "dynamic-tail:environment-contract"),
                dep(task_quality_id, "dynamic-tail:environment-tasks"),
            ),
            output_kind="EnvironmentReport",
        )
        code_integration = WorkItem(
            id=code_integration_id,
            agent_id="code_integration_agent",
            stage_id="integration",
            objective="按 Implementation Contract 的 ownership 和 wave 合并所有代码分区。",
            output_key="dynamic_implementation_merge",
            artifact_key="implementation",
            execution_mode=ExecutionMode.INTEGRATION,
            publish_target="workspace",
            dependencies=(
                dep(contract.id, "dynamic-tail:integration-contract"),
                dep(task_quality_id, "dynamic-tail:integration-tasks"),
                dep(environment_id, "dynamic-tail:integration-environment"),
            ),
            output_kind="WorkspaceSnapshot",
        )
        test = WorkItem(
            id=test_id,
            agent_id="test_agent",
            stage_id="test",
            objective="运行受信 sandbox 检查并保存测试证据。",
            output_key="dynamic_tests",
            artifact_key="tests",
            input_refs=(requirement_ref, ArtifactRef.published("tasks"), ArtifactRef.published("environment"), ArtifactRef.published("implementation")),
            dependencies=(
                dep(code_integration_id, "dynamic-tail:test-integration"),
                dep(task_quality_id, "dynamic-tail:test-tasks"),
                dep(environment_id, "dynamic-tail:test-environment"),
            ),
            output_kind="TestEvidence",
        )
        review_dependencies = [
            dep(contract.id, "dynamic-tail:review-contract"),
            dep(task_quality_id, "dynamic-tail:review-tasks"),
            dep(environment_id, "dynamic-tail:review-environment"),
            dep(code_integration_id, "dynamic-tail:review-integration"),
            dep(test_id, "dynamic-tail:review-test"),
        ]
        if requirement is not None:
            review_dependencies.insert(0, dep(requirement.id, "dynamic-tail:review-requirement"))
        if architecture is not None:
            review_dependencies.insert(
                1 if requirement is not None else 0,
                dep(architecture.id, "dynamic-tail:review-architecture"),
            )

        review = WorkItem(
            id=review_id,
            agent_id="review_agent",
            stage_id="review",
            objective="汇总需求、架构、合同、实现和测试证据，保存最终交付审查。",
            output_key="dynamic_review",
            artifact_key="review",
            input_refs=(requirement_ref, architecture_ref, contract_ref, ArtifactRef.published("tasks"), ArtifactRef.published("environment"), ArtifactRef.published("implementation"), ArtifactRef.published("tests")),
            dependencies=tuple(review_dependencies),
            output_kind="ReviewReport",
        )
        additions = (
            task_plan,
            task_integration,
            task_quality,
            environment,
            code_integration,
            test,
            review,
        )
        preserved = tuple(
            item
            for item in plan.work_items
            if item.agent_id not in self._REPLACED_AGENTS
        )
        existing_ids = {item.id for item in preserved}
        duplicate_ids = sorted(existing_ids.intersection(item.id for item in additions))
        if duplicate_ids:
            raise ValueError(
                "动态交付尾部节点 ID 与现有计划冲突: " + ", ".join(duplicate_ids)
            )
        expanded = ExecutionPlan(
            id=plan.id,
            goal=plan.goal,
            work_items=preserved + additions,
            template_id=plan.template_id,
            process_id=plan.process_id,
            trace=plan.trace,
        )
        return DeliveryTailExpansion(expanded, tuple(item.id for item in additions))

    @staticmethod
    def _resolve_item(
        plan: ExecutionPlan,
        item_id: str | None,
        predicate,
        *,
        required: bool,
    ) -> WorkItem | None:
        if item_id is not None:
            item = plan.work_item(item_id)
            if item is None:
                raise ValueError(f"动态交付尾部引用未知节点: {item_id}")
            return item
        matches = tuple(item for item in plan.work_items if predicate(item))
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise ValueError(
                "动态交付尾部需要唯一的需求/架构节点，"
                f"实际找到 {len(matches)} 个"
            )
        return matches[0]


__all__ = ["DeliveryTailExpansion", "DynamicDeliveryTailBuilder"]

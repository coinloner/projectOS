import unittest

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.planner.delivery_tail import DynamicDeliveryTailBuilder


class DynamicDeliveryTailBuilderTest(unittest.TestCase):
    def test_appends_process_tail_and_replaces_dynamic_placeholders(self) -> None:
        trace = TraceContext(requirement_id="req", trace_id="trace-tail")
        requirement = WorkItem(
            id="wi-requirement",
            agent_id="requirement_agent",
            objective="整理需求",
            output_key="requirement",
        )
        architecture = WorkItem(
            id="wi-architecture",
            agent_id="architecture_agent",
            stage_id="architecture_integration",
            objective="整合架构",
            output_key="architecture_candidate",
            artifact_key="architecture",
            execution_mode=ExecutionMode.INTEGRATION,
            publish_target="architecture",
        )
        contract = WorkItem(
            id="wi-contract",
            agent_id="architecture_contract_agent",
            stage_id="contract",
            objective="编译合同",
            output_key="architecture_contract",
            artifact_key="architecture_contract",
            execution_mode=ExecutionMode.INTEGRATION,
            publish_target="architecture_contract",
            dependencies=(
                WorkItemDependency("wi-architecture", DependencySource.SYSTEM),
            ),
        )
        # Planner may include a broad downstream placeholder. The control
        # plane must replace it with the canonical controlled tail.
        placeholder = WorkItem(
            id="wi-review-placeholder",
            agent_id="review_agent",
            stage_id="review",
            objective="完成审查",
            output_key="review",
            artifact_key="review",
            dependencies=(WorkItemDependency("wi-contract", DependencySource.SYSTEM),),
        )
        plan = ExecutionPlan(
            id="dynamic-tail-plan",
            goal="交付应用",
            work_items=(requirement, architecture, contract, placeholder),
            trace=trace,
        )
        expansion = DynamicDeliveryTailBuilder().append(
            plan,
            contract_item_id=contract.id,
            requirement_item_id=requirement.id,
            architecture_item_id=architecture.id,
        )
        ids = set(expansion.work_item_ids)
        self.assertEqual(
            ids,
            {
                "wi-dynamic-tail-tasks-plan",
                "wi-dynamic-tail-tasks-integration",
                "wi-dynamic-tail-tasks-quality",
                "wi-dynamic-tail-environment",
                "wi-dynamic-tail-code-integration",
                "wi-dynamic-tail-test",
                "wi-dynamic-tail-review",
            },
        )
        self.assertIsNone(expansion.plan.work_item("wi-review-placeholder"))
        task_plan = expansion.plan.work_item("wi-dynamic-tail-tasks-plan")
        environment = expansion.plan.work_item("wi-dynamic-tail-environment")
        review = expansion.plan.work_item("wi-dynamic-tail-review")
        assert task_plan is not None and environment is not None and review is not None
        self.assertIn(contract.id, task_plan.dependency_ids)
        self.assertIn("wi-dynamic-tail-tasks-quality", environment.dependency_ids)
        self.assertIn("wi-dynamic-tail-test", review.dependency_ids)
        self.assertEqual(
            {ref.ref_id for ref in task_plan.input_refs},
            {
                ArtifactRef.published("requirement").ref_id,
                ArtifactRef.published("architecture").ref_id,
                ArtifactRef.published("architecture_contract").ref_id,
            },
        )


if __name__ == "__main__":
    unittest.main()

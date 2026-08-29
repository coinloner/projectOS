import unittest

from app.orchestration.delivery_contract import DeliveryContract
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency


class DeliveryContractTest(unittest.TestCase):
    def test_project_delivery_uses_single_owner_for_each_final_document(self) -> None:
        contract = DeliveryContract.project_delivery()
        owners = {item.path: item.owner for item in contract.artifacts}
        self.assertEqual(owners["environment.md"], "environment")
        self.assertEqual(owners["implementation.md"], "code-integration")
        self.assertEqual(owners["tests.md"], "tests")
        self.assertEqual(owners["review.md"], "review")

    def test_owner_can_resolve_compiled_work_item_id(self) -> None:
        contract = DeliveryContract.project_delivery()
        items = (
            WorkItem(id="wi-project-documents", agent_id="agent", objective="docs", output_key="docs"),
            WorkItem(id="wi-environment", agent_id="agent", objective="env", output_key="env"),
            WorkItem(id="wi-code-integration", agent_id="agent", objective="merge", output_key="merge"),
            WorkItem(
                id="wi-tests", agent_id="agent", objective="tests", output_key="tests",
                dependencies=(
                    WorkItemDependency("wi-environment", DependencySource.SYSTEM),
                    WorkItemDependency("wi-code-integration", DependencySource.SYSTEM),
                ),
            ),
            WorkItem(
                id="wi-review", agent_id="agent", objective="review", output_key="review",
                dependencies=(WorkItemDependency("wi-tests", DependencySource.SYSTEM),),
            ),
        )
        contract.validate_plan(items)

    def test_missing_owner_is_rejected(self) -> None:
        contract = DeliveryContract.project_delivery()
        items = (WorkItem(id="wi-review", agent_id="review_agent", objective="review", output_key="review"),)
        with self.assertRaisesRegex(ValueError, "缺少产物 owner"):
            contract.validate_plan(items)


if __name__ == "__main__":
    unittest.main()

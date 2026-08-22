import unittest

from app.agent.registry import AgentDefinition, AgentRegistry
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.planner.patch import PlanPatch, PlanPatchError, apply_patch


def agents() -> AgentRegistry:
    registry = AgentRegistry()
    for agent_id, output_key in (("architecture_agent", "architecture"), ("test_agent", "tests")):
        registry.register(
            AgentDefinition(
                id=agent_id,
                domain=agent_id.removesuffix("_agent"),
                description=agent_id,
                output_key=output_key,
            ),
            factory=lambda: None,
        )
    return registry


def plan() -> ExecutionPlan:
    trace = TraceContext(requirement_id="req-patch", trace_id="tr-patch")
    return ExecutionPlan(
        id="plan-patch",
        goal="设计并验证 API",
        trace=trace,
        work_items=(
            WorkItem(id="wi-api", agent_id="architecture_agent", objective="设计 REST API", output_key="architecture"),
            WorkItem(
                id="wi-test",
                agent_id="test_agent",
                objective="测试 API",
                output_key="tests",
                dependencies=(
                    WorkItemDependency("wi-api", DependencySource.PLANNER),
                ),
            ),
        ),
    )


class PlannerPatchTest(unittest.TestCase):
    def test_modify_invalidates_changed_node_and_descendants(self) -> None:
        result = apply_patch(
            plan(),
            PlanPatch.model_validate(
                {
                    "rationale": "改用 GraphQL",
                    "base_plan_id": "plan-patch",
                    "operations": [
                        {
                            "operation": "modify",
                            "work_item_id": "wi-api",
                            "objective": "设计 GraphQL API",
                        }
                    ],
                }
            ),
            agents=agents(),
        )

        self.assertEqual(result.plan.work_item("wi-api").objective, "设计 GraphQL API")
        self.assertEqual(set(result.invalidated_work_item_ids), {"wi-api", "wi-test"})

    def test_completed_work_item_cannot_be_modified(self) -> None:
        patch = PlanPatch.model_validate(
            {
                "rationale": "修改已完成节点",
                "base_plan_id": "plan-patch",
                "operations": [
                    {"operation": "modify", "work_item_id": "wi-api", "objective": "改动"}
                ],
            }
        )
        with self.assertRaisesRegex(PlanPatchError, "已完成"):
            apply_patch(plan(), patch, agents=agents(), completed_work_item_ids={"wi-api"})

    def test_terminal_revision_can_recompute_completed_subgraph(self) -> None:
        patch = PlanPatch.model_validate(
            {
                "rationale": "修改已完成 API",
                "base_plan_id": "plan-patch",
                "operations": [
                    {"operation": "modify", "work_item_id": "wi-api", "objective": "设计 GraphQL API"}
                ],
            }
        )
        result = apply_patch(
            plan(),
            patch,
            agents=agents(),
            completed_work_item_ids={"wi-api", "wi-test"},
            allow_completed_revision=True,
        )
        self.assertEqual(set(result.invalidated_work_item_ids), {"wi-api", "wi-test"})

    def test_removed_node_with_dependents_is_rejected(self) -> None:
        patch = PlanPatch.model_validate(
            {
                "rationale": "删除 API",
                "base_plan_id": "plan-patch",
                "operations": [{"operation": "remove", "work_item_id": "wi-api"}],
            }
        )
        with self.assertRaisesRegex(PlanPatchError, "依赖"):
            apply_patch(plan(), patch, agents=agents())


if __name__ == "__main__":
    unittest.main()

import unittest
from dataclasses import replace

from app.agent.registry import AgentDefinition, AgentRegistry
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.planner.patch import (
    PlanPatch,
    PlanPatchError,
    RepairPlanPatch,
    apply_patch,
    apply_repair_patch,
)


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
    def test_repair_patch_appends_only_new_work_items(self) -> None:
        patch = RepairPlanPatch.parse(
            '{"rationale":"修复","base_plan_id":"plan-patch","repair_scope":["wi-api"],'
            '"operations":[{"operation":"add","ref":"fix","agent_id":"test_agent",'
            '"objective":"重新验证","depends_on":[]}]}'
        )
        repaired = apply_repair_patch(plan(), patch, agents=agents(), plan_id="repair-1")
        self.assertEqual(repaired.id, "repair-1")
        self.assertEqual([item.id for item in repaired.work_items], ["wi-repair-01-tests"])
        self.assertEqual(repaired.trace, plan().trace)

    def test_repair_patch_dependencies_must_reference_same_patch(self) -> None:
        patch = RepairPlanPatch.parse(
            '{"rationale":"修复","base_plan_id":"plan-patch","operations":['
            '{"operation":"add","ref":"fix","agent_id":"test_agent",'
            '"objective":"重新验证","depends_on":["wi-api"]}]}'
        )
        with self.assertRaisesRegex(PlanPatchError, "同一补丁"):
            apply_repair_patch(plan(), patch, agents=agents())

    def test_work_item_contract_digest_is_stable_for_objective_diagnostics(self) -> None:
        item = plan().work_item("wi-api")
        assert item is not None
        revised = replace(item, objective="修复后的 API 设计")
        self.assertEqual(revised.contract_digest, item.contract_digest)

    def test_work_item_rejects_tampered_contract_digest(self) -> None:
        item = plan().work_item("wi-api")
        assert item is not None
        with self.assertRaisesRegex(ValueError, "contract_digest"):
            replace(item, contract_digest="tampered")

    def test_work_item_rejects_permission_change_with_old_digest(self) -> None:
        item = plan().work_item("wi-api")
        assert item is not None
        with self.assertRaisesRegex(ValueError, "contract_digest"):
            replace(item, allowed_paths=("workspace/**",))

    def test_work_item_rejects_overlapping_allowed_and_forbidden_scopes(self) -> None:
        with self.assertRaisesRegex(ValueError, "重叠"):
            WorkItem(
                id="wi-scope",
                agent_id="code_agent",
                objective="实现",
                output_key="implementation",
                allowed_paths=("workspace/**",),
                forbidden_paths=("workspace/backend/**",),
            )

    def test_dependency_changes_are_rejected_by_frozen_contract(self) -> None:
        patch = PlanPatch.model_validate(
            {
                "rationale": "改变依赖",
                "base_plan_id": "plan-patch",
                "operations": [
                    {"operation": "modify", "work_item_id": "wi-test", "depends_on": ["wi-api", "wi-extra"]}
                ],
            }
        )
        with self.assertRaisesRegex(PlanPatchError, "合同已冻结"):
            apply_patch(plan(), patch, agents=agents())

    def test_repair_scope_rejects_out_of_scope_operations(self) -> None:
        patch = PlanPatch.model_validate(
            {
                "rationale": "局部修复",
                "base_plan_id": "plan-patch",
                "repair_scope": ["wi-test"],
                "operations": [
                    {"operation": "modify", "work_item_id": "wi-api", "objective": "越界"}
                ],
            }
        )
        with self.assertRaisesRegex(PlanPatchError, "超出 repair_scope"):
            apply_patch(plan(), patch, agents=agents())

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

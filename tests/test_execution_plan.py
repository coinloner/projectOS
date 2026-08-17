import unittest

from app.workflow.plan import ExecutionPlan
from app.workflow.work_item import (
    DependencySource,
    WorkItem,
    WorkItemDependency,
)


def make_item(
    item_id: str, *, depends_on: tuple[str, ...] = ()
) -> WorkItem:
    return WorkItem(
        id=item_id,
        agent_id="requirement_agent",
        objective="生成需求草稿",
        output_key=f"{item_id}_output",
        policy_id="requirement_draft_v1",
        dependencies=tuple(
            WorkItemDependency(
                work_item_id=dependency,
                source=DependencySource.PLANNER,
            )
            for dependency in depends_on
        ),
    )


class ExecutionPlanTest(unittest.TestCase):
    def test_plan_exposes_work_items_and_roots(self) -> None:
        draft = make_item("requirement_draft")
        review = make_item("requirement_review", depends_on=(draft.id,))
        plan = ExecutionPlan(
            id="requirement-plan",
            goal="生成可确认的需求草稿",
            template_id="requirement_draft",
            work_items=(draft, review),
        )

        self.assertEqual(plan.work_item("requirement_review"), review)
        self.assertIsNone(plan.work_item("missing"))
        self.assertEqual(plan.root_items(), (draft,))

    def test_item_rejects_self_and_duplicate_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能依赖自身"):
            make_item("draft", depends_on=("draft",))

        with self.assertRaisesRegex(ValueError, "重复依赖"):
            make_item("draft", depends_on=("input", "input"))

    def test_plan_rejects_duplicate_work_item_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复 WorkItem id"):
            ExecutionPlan(
                id="duplicate-plan",
                goal="测试",
                work_items=(make_item("draft"), make_item("draft")),
            )

    def test_plan_rejects_unknown_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "依赖不存在的工作项: missing"):
            ExecutionPlan(
                id="unknown-dependency-plan",
                goal="测试",
                work_items=(make_item("draft", depends_on=("missing",)),),
            )

    def test_plan_rejects_cycles(self) -> None:
        with self.assertRaisesRegex(ValueError, "循环依赖"):
            ExecutionPlan(
                id="cyclic-plan",
                goal="测试",
                work_items=(
                    make_item("draft", depends_on=("review",)),
                    make_item("review", depends_on=("draft",)),
                ),
            )


if __name__ == "__main__":
    unittest.main()

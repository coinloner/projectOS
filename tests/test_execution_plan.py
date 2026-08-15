import unittest

from app.workflow.plan import ExecutionPlan, TaskNode


def make_node(
    node_id: str, *, depends_on: tuple[str, ...] = ()
) -> TaskNode:
    return TaskNode(
        id=node_id,
        agent_id="requirement_agent",
        objective="生成需求草稿",
        output_key=f"{node_id}_output",
        policy_id="requirement_draft_v1",
        depends_on=depends_on,
    )


class ExecutionPlanTest(unittest.TestCase):
    def test_plan_exposes_nodes_and_roots(self) -> None:
        draft = make_node("requirement_draft")
        review = make_node("requirement_review", depends_on=(draft.id,))
        plan = ExecutionPlan(
            id="requirement-plan",
            goal="生成可确认的需求草稿",
            template_id="requirement_draft",
            nodes=(draft, review),
        )

        self.assertEqual(plan.node("requirement_review"), review)
        self.assertIsNone(plan.node("missing"))
        self.assertEqual(plan.root_nodes(), (draft,))

    def test_node_rejects_self_and_duplicate_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能依赖自身"):
            make_node("draft", depends_on=("draft",))

        with self.assertRaisesRegex(ValueError, "重复依赖"):
            make_node("draft", depends_on=("input", "input"))

    def test_plan_rejects_duplicate_node_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复节点 id"):
            ExecutionPlan(
                id="duplicate-plan",
                goal="测试",
                nodes=(make_node("draft"), make_node("draft")),
            )

    def test_plan_rejects_unknown_dependencies(self) -> None:
        with self.assertRaisesRegex(ValueError, "依赖不存在的节点: missing"):
            ExecutionPlan(
                id="unknown-dependency-plan",
                goal="测试",
                nodes=(make_node("draft", depends_on=("missing",)),),
            )

    def test_plan_rejects_cycles(self) -> None:
        with self.assertRaisesRegex(ValueError, "循环依赖"):
            ExecutionPlan(
                id="cyclic-plan",
                goal="测试",
                nodes=(
                    make_node("draft", depends_on=("review",)),
                    make_node("review", depends_on=("draft",)),
                ),
            )


if __name__ == "__main__":
    unittest.main()

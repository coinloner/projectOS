"""测试 delivery_incremental 和 architecture_only 模板。"""

import unittest
from app.workflow.templates import (
    delivery_incremental_template,
    architecture_only_template,
)


class NewTemplatesTest(unittest.TestCase):
    """测试新增的两种交付模式。"""

    def test_delivery_incremental_compiles(self) -> None:
        """验证 delivery_incremental 模板可以编译。"""
        template = delivery_incremental_template()

        # 验证模板基本属性
        self.assertEqual(template.id, "delivery_incremental")
        self.assertEqual(template.name, "增量项目交付")

        # 验证跳过了 requirement 和 architecture 阶段
        node_ids = [node.id for node in template.nodes]
        self.assertNotIn("requirement", node_ids, "增量模板应该跳过 requirement")
        self.assertNotIn("architecture", node_ids, "增量模板应该跳过 architecture")
        self.assertNotIn("architecture-blueprint", node_ids, "增量模板应该跳过 architecture")
        self.assertNotIn("contract", node_ids, "增量模板应该跳过 contract")

        # 验证包含核心阶段
        self.assertIn("tasks-plan", node_ids)
        self.assertIn("environment", node_ids)
        self.assertIn("implementation-anchor", node_ids)
        self.assertIn("tests", node_ids)
        self.assertIn("review", node_ids)

    def test_architecture_only_compiles(self) -> None:
        """验证 architecture_only 模板可以编译。"""
        template = architecture_only_template()

        # 验证模板基本属性
        self.assertEqual(template.id, "architecture_only")
        self.assertEqual(template.name, "仅架构设计")

        # 验证只包含需求和架构阶段
        node_ids = [node.id for node in template.nodes]
        self.assertIn("requirement", node_ids)
        self.assertIn("architecture-blueprint", node_ids)
        self.assertIn("architecture-quality-gate", node_ids)

        # 验证不包含代码和测试阶段
        self.assertNotIn("tasks", node_ids, "架构设计模板不应该有 tasks")
        self.assertNotIn("environment", node_ids, "架构设计模板不应该有 environment")
        self.assertNotIn("implementation", node_ids, "架构设计模板不应该有 implementation")
        self.assertNotIn("tests", node_ids, "架构设计模板不应该有 tests")
        self.assertNotIn("review", node_ids, "架构设计模板不应该有 review")

    def test_architecture_only_is_minimal(self) -> None:
        """验证 architecture_only 模板只有 4 个节点。"""
        template = architecture_only_template()
        self.assertEqual(len(template.nodes), 4,
                        "architecture_only 应该只有 4 个节点: requirement, blueprint, integration, quality-gate")


if __name__ == "__main__":
    unittest.main()

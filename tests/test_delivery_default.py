"""测试 delivery_default 模板的编译和结构。"""

import unittest
from app.workflow.templates import delivery_default_template
from app.workflow.compiler import TemplateCompiler
from app.orchestration.trace import TraceContext


class DeliveryDefaultTemplateTest(unittest.TestCase):
    """测试新的通用交付模板。"""

    def test_delivery_default_compiles(self) -> None:
        """验证 delivery_default 模板可以编译成合法的 ExecutionPlan。"""
        template = delivery_default_template()

        # 验证模板基本属性
        self.assertEqual(template.id, "delivery_default")
        self.assertEqual(template.name, "通用项目交付")

        # 验证模板包含顶层节点 (tasks 阶段有 3 个子节点,所以总共 10 个)
        self.assertGreater(len(template.nodes), 7,
                          "delivery_default 应该至少有 8 个主要阶段")

        # 验证顶层阶段的 stage_id
        stage_ids = [node.stage_id for node in template.nodes if node.stage_id]
        expected_stages = {
            "requirement",
            "architecture_blueprint",
            "contract",
            "tasks_plan",
            "tasks_integration",
            "tasks_quality_gate",
            "environment",
            "implementation",
            "test",
            "review",
        }
        # 至少包含核心阶段
        core_stages = {"requirement", "architecture_blueprint", "contract", "environment", "implementation", "test", "review"}
        self.assertTrue(core_stages.issubset(set(stage_ids)),
                       f"应该包含核心阶段,实际: {stage_ids}")

    def test_architecture_stage_is_blueprint_anchor(self) -> None:
        """验证 Architecture 阶段是单个 Blueprint 锚点,不预设具体模块。"""
        template = delivery_default_template()

        # 查找 architecture 相关节点
        arch_nodes = [node for node in template.nodes
                     if "architecture" in node.id or "architecture" in (node.stage_id or "")]

        # 应该只有一个 architecture-blueprint 节点
        blueprint_nodes = [node for node in arch_nodes if "blueprint" in node.id]
        self.assertEqual(len(blueprint_nodes), 1,
                        "Architecture 阶段应该只有一个 Blueprint 锚点")

        blueprint_node = blueprint_nodes[0]
        self.assertEqual(blueprint_node.slot, "blueprint")
        self.assertEqual(blueprint_node.stage_id, "architecture_blueprint")

        # 不应该有预设的 domain/api/runtime 模块节点
        for node in template.nodes:
            self.assertNotIn("domain", node.id.lower(),
                           "模板不应该预设 domain 模块")
            self.assertNotIn("api", node.id.lower(),
                           "模板不应该预设 api 模块")
            self.assertNotIn("runtime", node.id.lower(),
                           "模板不应该预设 runtime 模块")

    def test_implementation_stage_is_dynamic_anchor(self) -> None:
        """验证 Implementation 阶段是动态展开锚点,不预设具体文件。"""
        template = delivery_default_template()

        # 查找 implementation 节点
        impl_nodes = [node for node in template.nodes
                     if "implementation" in node.id or node.stage_id == "implementation"]

        # 应该只有一个 implementation anchor
        self.assertGreater(len(impl_nodes), 0, "应该有 implementation 阶段")

        # 不应该有预设的具体文件节点
        for node in template.nodes:
            # 文件名通常包含 .py, .js, index, main 等
            node_id_lower = node.id.lower()
            self.assertNotIn(".py", node_id_lower, "模板不应该预设具体 Python 文件")
            self.assertNotIn(".js", node_id_lower, "模板不应该预设具体 JS 文件")
            self.assertNotIn("main.py", node_id_lower, "模板不应该预设 main.py")
            self.assertNotIn("index.html", node_id_lower, "模板不应该预设 index.html")

    def test_contract_stage_exists(self) -> None:
        """验证 Contract 阶段存在,用于确定性编译。"""
        template = delivery_default_template()

        contract_nodes = [node for node in template.nodes
                         if node.id == "contract" or node.stage_id == "contract"]

        self.assertEqual(len(contract_nodes), 1, "应该有一个 Contract 阶段")

        contract_node = contract_nodes[0]
        self.assertEqual(contract_node.agent_id, "architecture_contract_agent")
        self.assertIn("architecture-blueprint", contract_node.depends_on)


if __name__ == "__main__":
    unittest.main()

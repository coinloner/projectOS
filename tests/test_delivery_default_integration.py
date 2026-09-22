"""集成测试 - 验证 delivery_default 模板的完整编译流程。"""

import unittest
from app.workflow.templates import delivery_default_template
from app.workflow.compiler import TemplateCompiler
from app.orchestration.trace import TraceContext
from app.agent.registry import AgentDefinition


class DeliveryDefaultIntegrationTest(unittest.TestCase):
    """测试 delivery_default 模板的编译和执行计划生成。"""

    def test_delivery_default_compiles_to_execution_plan(self) -> None:
        """验证 delivery_default 可以编译成合法的 ExecutionPlan。"""
        template = delivery_default_template()

        # 创建最小的 agent 定义集合
        agents = {
            "requirement_agent": AgentDefinition(
                id="requirement_agent",
                domain="requirement",
                description="澄清需求",
                output_key="requirement",
            ),
            "architecture_agent": AgentDefinition(
                id="architecture_agent",
                domain="architecture",
                description="设计架构",
                output_key="architecture",
            ),
            "architecture_contract_agent": AgentDefinition(
                id="architecture_contract_agent",
                domain="architecture",
                description="编译架构合同",
                output_key="architecture_contract",
            ),
            "task_agent": AgentDefinition(
                id="task_agent",
                domain="task",
                description="规划任务",
                output_key="tasks",
            ),
            "bootstrap_agent": AgentDefinition(
                id="bootstrap_agent",
                domain="bootstrap",
                description="准备环境",
                output_key="environment",
            ),
            "integration_agent": AgentDefinition(
                id="integration_agent",
                domain="code",
                description="集成实现",
                output_key="implementation",
            ),
            "test_agent": AgentDefinition(
                id="test_agent",
                domain="test",
                description="执行测试",
                output_key="tests",
            ),
            "review_agent": AgentDefinition(
                id="review_agent",
                domain="review",
                description="审查交付",
                output_key="review",
            ),
        }

        # 创建编译器并编译
        compiler = TemplateCompiler()
        trace = TraceContext.ephemeral()

        try:
            plan = compiler.compile(
                template=template,
                goal="实现一个 Todo 应用",
                trace=trace,
                plan_id="test-plan",
                agent_output_keys={agent_id: agent.output_key for agent_id, agent in agents.items()},
            )

            # 验证生成了合法的 ExecutionPlan
            self.assertIsNotNone(plan)
            self.assertEqual(plan.template_id, "delivery_default")
            self.assertGreater(len(plan.work_items), 0, "应该生成至少一个 WorkItem")

            # 验证包含核心阶段的 WorkItem (检查 ID 中包含关键词)
            item_ids = [item.id for item in plan.work_items]
            self.assertTrue(any("requirement" in item_id for item_id in item_ids))
            self.assertTrue(any("architecture-blueprint" in item_id for item_id in item_ids))
            self.assertTrue(any("contract" in item_id for item_id in item_ids))
            self.assertTrue(any("tasks" in item_id for item_id in item_ids))
            self.assertTrue(any("environment" in item_id for item_id in item_ids))
            self.assertTrue(any("implementation" in item_id for item_id in item_ids))
            self.assertTrue(any("tests" in item_id for item_id in item_ids))
            self.assertTrue(any("review" in item_id for item_id in item_ids))

            print(f"✓ delivery_default 成功编译")
            print(f"  生成 {len(plan.work_items)} 个 WorkItem")
            print(f"  WorkItem IDs: {item_ids}")

        except Exception as e:
            self.fail(f"模板编译失败: {e}")


if __name__ == "__main__":
    unittest.main()

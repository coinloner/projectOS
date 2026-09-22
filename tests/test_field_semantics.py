import unittest

from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    LayerDecision,
    ModuleRef,
)
from app.orchestration.field_semantics import (
    NodeExecutionContract,
    SemanticFieldSpec,
    SemanticRegistry,
    compile_node_contract,
)
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import WorkItem
from app.planner.dynamic_builder import BlueprintValidationError, BlueprintValidator


class SemanticRegistryTest(unittest.TestCase):
    def test_default_registry_exposes_shared_and_node_fields(self) -> None:
        registry = SemanticRegistry.default()

        self.assertEqual(registry.get("goal").consumer, "所有节点")
        self.assertIn("purpose", registry.fields_for("architecture_agent"))
        self.assertIn("changed_files", registry.fields_for("code_integration_agent"))
        self.assertIsNone(registry.get("does_not_exist"))

    def test_registry_rejects_duplicate_registration(self) -> None:
        registry = SemanticRegistry()
        field = SemanticFieldSpec("业务目标", "Trace", "Agent")
        registry.register("goal", field)
        with self.assertRaises(ValueError):
            registry.register("goal", field)
        with self.assertRaises(ValueError):
            registry.register_node_fields("architecture_agent", {"goal": field})
        with self.assertRaises(ValueError):
            registry.register_node_fields("architecture_agent", {"goal": field})

    def test_custom_registry_is_used_when_compiling_node_contract(self) -> None:
        registry = SemanticRegistry.default()
        registry.register_node_fields(
            "custom_agent",
            {"tenant_id": SemanticFieldSpec("租户边界", "Trace", "custom_agent", True)},
        )
        item = WorkItem(
            id="custom",
            agent_id="custom_agent",
            objective="执行租户范围任务",
            output_key="custom",
            execution_mode=ExecutionMode.EXCLUSIVE,
        )
        plan = ExecutionPlan(
            id="semantic-plan",
            goal="语义测试",
            work_items=(item,),
            trace=TraceContext(requirement_id="req", trace_id="trace"),
        )
        contract = compile_node_contract(type("State", (), {"plan": plan})(), item, registry)
        self.assertIsInstance(contract, NodeExecutionContract)
        self.assertEqual(contract.fields["tenant_id"].meaning, "租户边界")

    def test_blueprint_validation_checks_layer_and_module_relationships(self) -> None:
        """测试 BlueprintValidator 检查模块依赖关系(层依赖已在 pydantic 层校验)。"""
        blueprint = ArchitectureBlueprint(
            schema_version=1,
            design_id="bp",
            system_boundary="订单服务",
            layers=[
                LayerDecision(name="api", allowed_dependencies=[]),
            ],
            modules=[
                ModuleRef(
                    module_id="orders",
                    responsibility="订单规则",
                    depends_on_modules=["missing"],
                )
            ],
        )
        with self.assertRaises(BlueprintValidationError) as raised:
            BlueprintValidator().validate(blueprint)
        self.assertIn("语义校验失败", str(raised.exception))
        self.assertIn("未声明", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

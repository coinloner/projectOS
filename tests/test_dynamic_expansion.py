"""测试动态展开基础设施:Blueprint→Modules 和 Contract→Files。

验证 DynamicPlanBuilder 和 Contract 编译器能够正确动态生成 WorkItem。
"""

import unittest
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    LayerDecision,
    ModuleRef,
)
from app.planner.dynamic_builder import DynamicPlanBuilder
from app.workflow.compiler import TemplateCompiler
from app.orchestration.work_item import WorkItem, ExecutionMode
from app.orchestration.trace import TraceContext
from app.orchestration.plan import ExecutionPlan


class DynamicExpansionTest(unittest.TestCase):
    """验证动态展开基础设施已 ready。"""

    def test_blueprint_to_dynamic_modules(self) -> None:
        """给定 Blueprint (3个模块),验证 DynamicPlanBuilder 生成 3 个 ModuleDesign WorkItem。"""
        # 构造一个简单的 Blueprint
        blueprint = ArchitectureBlueprint(
            schema_version=1,
            design_id="test-blueprint",
            system_boundary="Todo 命令行应用",
            layers=[
                LayerDecision(
                    name="presentation",
                    allowed_dependencies=["application"],
                    path_mapping=["cli/**"],
                ),
                LayerDecision(
                    name="application",
                    allowed_dependencies=["data"],
                    path_mapping=["core/**"],
                ),
                LayerDecision(
                    name="data",
                    allowed_dependencies=[],
                    path_mapping=["storage/**"],
                ),
            ],
            modules=[
                ModuleRef(
                    module_id="cli_interface",
                    responsibility="命令行交互",
                    purpose="接收用户命令并展示结果",
                ),
                ModuleRef(
                    module_id="todo_logic",
                    responsibility="Todo 业务逻辑",
                    purpose="处理添加、列出、完成、删除任务",
                    depends_on_modules=["task_storage"],
                ),
                ModuleRef(
                    module_id="task_storage",
                    responsibility="任务数据存储",
                    purpose="维护内存中的任务集合",
                ),
            ],
        )

        # 构造一个最小计划:只有一个 blueprint WorkItem
        trace = TraceContext(requirement_id="req-test", trace_id="trace-test")
        blueprint_item = WorkItem(
            id="wi-blueprint",
            agent_id="architecture_agent",
            objective="生成架构蓝图",
            output_key="architecture",
            execution_mode=ExecutionMode.PARTITIONED,
            stage_id="architecture_blueprint",
            slot="blueprint",
            output_kind="ArchitectureBlueprint",
        )

        # 构造基础计划
        base_plan = ExecutionPlan(
            id="test-plan",
            goal="测试 Blueprint 动态展开",
            work_items=(blueprint_item,),
            trace=trace,
        )

        # 调用 DynamicPlanBuilder.expand_modules
        builder = DynamicPlanBuilder()
        expansion = builder.expand_modules(
            base_plan,
            blueprint,
            blueprint_work_item_id=blueprint_item.id,
            integration_work_item_id=None,
            parent_plan_revision=1,
        )

        # 验证:应该生成 3 个 ModuleDesign WorkItem
        expanded_plan = expansion.plan
        module_items = [
            item for item in expanded_plan.work_items
            if item.stage_id == "architecture_module" or (item.slot or "").startswith("module-")
        ]

        self.assertEqual(len(module_items), 3, "应该生成 3 个 ModuleDesign WorkItem")

        # 验证模块 ID 正确
        module_ids = {item.slot.replace("module-", "") if item.slot else "" for item in module_items}
        self.assertIn("cli_interface", module_ids)
        self.assertIn("todo_logic", module_ids)
        self.assertIn("task_storage", module_ids)

    def test_contract_provides_implementation_units(self) -> None:
        """验证 Contract 包含 implementation_units,可以用于动态生成文件级 WorkItem。

        这个测试不直接测试文件级展开(那是 Runner 的职责),
        只验证 Contract 数据结构包含了展开所需的信息。
        """
        from app.domain.architecture.implementation_contract import (
            ImplementationContract,
            ImplementationUnit,
        )

        # 构造一个简单的 Contract (3个 implementation_units)
        contract = ImplementationContract(
            schema_version=1,
            units=(
                ImplementationUnit(
                    unit_id="u-cli-main",
                    layer="presentation",
                    objective="实现命令行入口",
                    allowed_paths=("cli/**",),
                    owned_files=("cli/main.py",),
                    wave=1,
                    depends_on=(),
                ),
                ImplementationUnit(
                    unit_id="u-core-logic",
                    layer="application",
                    objective="实现 Todo 逻辑",
                    allowed_paths=("core/**",),
                    owned_files=("core/todo.py",),
                    wave=1,
                    depends_on=("u-storage",),
                ),
                ImplementationUnit(
                    unit_id="u-storage",
                    layer="data",
                    objective="实现任务存储",
                    allowed_paths=("storage/**",),
                    owned_files=("storage/memory.py",),
                    wave=1,
                    depends_on=(),
                ),
            ),
        )

        # 验证 Contract 包含足够的信息用于文件级展开
        self.assertEqual(len(contract.units), 3)

        # 验证每个 unit 都有 owned_files (这是文件级展开的基础)
        for unit in contract.units:
            self.assertIsInstance(unit.owned_files, tuple)
            self.assertGreater(len(unit.owned_files), 0, f"unit {unit.unit_id} 必须有 owned_files")

        # 验证 wave 信息存在 (用于控制并行度)
        for unit in contract.units:
            self.assertIsInstance(unit.wave, int)


if __name__ == "__main__":
    unittest.main()

import unittest
import tempfile
from dataclasses import replace

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    ImplementationDesign,
    LayerDecision,
    ModuleDesign,
    ModuleRef,
)
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.orchestration.state import RunState
from app.orchestration.node_result import NodeResult
from app.agent.registry import AgentRegistry
from app.agent.registry import AgentDefinition
from app.agent.result import AgentResult
from app.tool_manager.gateway import ToolGateway
from app.orchestration.runner import GraphRunner
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.execution_context import ExecutionContext
from app.artifact.store import ArtifactStore
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft
from app.planner.validator import PlanValidator
from app.workflow.template import WorkflowTemplateRegistry
from app.planner.dynamic_builder import (
    BlueprintValidationError,
    DynamicPlanBuilder,
)


def _blueprint(*modules: ModuleRef) -> ArchitectureBlueprint:
    return ArchitectureBlueprint(
        schema_version=1,
        design_id="bp-shop",
        system_boundary="电商系统负责商品浏览和订单创建。",
        layers=[LayerDecision(name="domain"), LayerDecision(name="api")],
        modules=list(modules),
    )


def _base_plan(with_integration: bool = True, with_contract: bool = False) -> ExecutionPlan:
    trace = TraceContext(requirement_id="req", trace_id="tr-dynamic")
    blueprint = WorkItem(
        id="wi-blueprint",
        agent_id="architecture_agent",
        stage_id="architecture_blueprint",
        objective="生成总体蓝图",
        output_key="architecture_blueprint",
        artifact_key="architecture",
        execution_mode=ExecutionMode.PARTITIONED,
        slot="blueprint",
        input_refs=(ArtifactRef.published("requirement"),),
        output_kind="ArchitectureBlueprint",
    )
    items = [blueprint]
    if with_integration:
        items.append(
            WorkItem(
                id="wi-architecture-integration",
                agent_id="architecture_agent",
                stage_id="architecture_integration",
                objective="整合架构",
                output_key="architecture_candidate",
                artifact_key="architecture",
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture",
                dependencies=(
                    WorkItemDependency(
                        "wi-blueprint", DependencySource.SYSTEM, "blueprint"
                    ),
                ),
            )
        )
    if with_contract:
        items.append(
            WorkItem(
                id="wi-contract",
                agent_id="architecture_contract_agent",
                stage_id="contract",
                objective="编译实现合同",
                output_key="architecture_contract",
                artifact_key="architecture_contract",
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture_contract",
                dependencies=(
                    WorkItemDependency(
                        "wi-architecture-integration", DependencySource.SYSTEM, "integration"
                    ),
                ),
                output_kind="ImplementationContract",
            )
        )
    return ExecutionPlan(
        id="plan-dynamic",
        goal="构建电商系统",
        work_items=tuple(items),
        process_id="software_delivery",
        trace=trace,
    )


class DynamicBuilderTest(unittest.TestCase):
    def test_expands_modules_with_business_purpose_and_waves(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="商品目录", purpose="让用户发现商品"),
            ModuleRef(
                module_id="orders",
                responsibility="订单编排",
                purpose="让用户完成购买",
                depends_on_modules=["catalog"],
            ),
        )
        expansion = DynamicPlanBuilder().expand_modules(
            _base_plan(with_contract=True), blueprint, blueprint_work_item_id="wi-blueprint",
            integration_work_item_id="wi-architecture-integration",
        )
        self.assertEqual(expansion.module_waves, {"catalog": 0, "orders": 1})
        catalog = expansion.plan.work_item("wi-architecture-module-catalog")
        orders = expansion.plan.work_item("wi-architecture-module-orders")
        self.assertIsNotNone(catalog)
        self.assertIn("让用户发现商品", catalog.objective)
        self.assertEqual(
            catalog.delivery_contract["architecture"]["purpose"], "让用户发现商品"
        )
        self.assertEqual(orders.dependency_ids, ("wi-blueprint", "wi-architecture-module-catalog"))
        integration = expansion.plan.work_item("wi-architecture-integration")
        self.assertEqual(len(integration.input_refs), 3)
        self.assertEqual(len(integration.dependencies), 3)

    def test_rejects_unknown_and_cyclic_module_dependencies(self) -> None:
        with self.assertRaises(BlueprintValidationError):
            DynamicPlanBuilder().expand_modules(
                _base_plan(False),
                _blueprint(ModuleRef(module_id="api", responsibility="接口", depends_on_modules=["missing"])),
                blueprint_work_item_id="wi-blueprint",
            )
        with self.assertRaises(BlueprintValidationError):
            DynamicPlanBuilder().expand_modules(
                _base_plan(False),
                _blueprint(
                    ModuleRef(module_id="a", responsibility="A", depends_on_modules=["b"]),
                    ModuleRef(module_id="b", responsibility="B", depends_on_modules=["a"]),
                ),
                blueprint_work_item_id="wi-blueprint",
            )

    def test_expansion_is_idempotence_guarded(self) -> None:
        blueprint = _blueprint(ModuleRef(module_id="api", responsibility="接口"))
        first = DynamicPlanBuilder().expand_modules(
            _base_plan(False), blueprint, blueprint_work_item_id="wi-blueprint"
        )
        with self.assertRaises(BlueprintValidationError):
            DynamicPlanBuilder().expand_modules(
                first.plan, blueprint, blueprint_work_item_id="wi-blueprint"
            )

    def test_expands_implementation_designs_after_module_designs(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"),
            ModuleRef(module_id="orders", responsibility="订单", purpose="完成购买", depends_on_modules=["catalog"]),
        )
        module_expansion = DynamicPlanBuilder().expand_modules(
            _base_plan(with_contract=True), blueprint, blueprint_work_item_id="wi-blueprint",
            integration_work_item_id="wi-architecture-integration",
        )
        designs = [
            ModuleDesign(
                schema_version=1,
                design_id=f"module-{module.module_id}",
                parent_design_id=blueprint.design_id,
                module_id=module.module_id,
                purpose=module.purpose,
                responsibilities=[module.module_id],
                depends_on_modules=module.depends_on_modules,
            )
            for module in blueprint.modules
        ]
        expansion = DynamicPlanBuilder().expand_implementations(
            module_expansion.plan,
            blueprint,
            designs,
            blueprint_work_item_id="wi-blueprint",
            module_work_item_ids={
                "catalog": "wi-architecture-module-catalog",
                "orders": "wi-architecture-module-orders",
            },
            integration_work_item_id="wi-architecture-integration",
        )
        self.assertEqual(
            set(expansion.added_work_item_ids),
            {
                "wi-architecture-implementation-catalog",
                "wi-architecture-implementation-orders",
            },
        )
        catalog = expansion.plan.work_item("wi-architecture-implementation-catalog")
        orders = expansion.plan.work_item("wi-architecture-implementation-orders")
        self.assertEqual(catalog.stage_id, "architecture_implementation")
        self.assertEqual(catalog.output_kind, "ImplementationDesign")
        self.assertEqual(catalog.dependency_ids, ("wi-architecture-module-catalog",))
        self.assertEqual(orders.dependency_ids, ("wi-architecture-module-orders",))
        self.assertEqual(orders.wave, 1)
        integration = expansion.plan.work_item("wi-architecture-integration")
        self.assertIn("wi-architecture-implementation-catalog", integration.dependency_ids)
        self.assertIn("wi-architecture-implementation-orders", integration.dependency_ids)
        self.assertEqual(len(integration.input_refs), 5)
        contract = expansion.plan.work_item("wi-contract")
        self.assertEqual(contract.input_refs, integration.input_refs)

    def test_implementation_expansion_rejects_missing_or_mismatched_module_design(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"),
            ModuleRef(module_id="orders", responsibility="订单", purpose="完成购买", depends_on_modules=["catalog"]),
        )
        module_expansion = DynamicPlanBuilder().expand_modules(
            _base_plan(), blueprint, blueprint_work_item_id="wi-blueprint",
            integration_work_item_id="wi-architecture-integration",
        )
        valid = ModuleDesign(
            schema_version=1,
            design_id="module-catalog",
            parent_design_id=blueprint.design_id,
            module_id="catalog",
            purpose="发现商品",
            responsibilities=["catalog"],
        )
        with self.assertRaises(BlueprintValidationError):
            DynamicPlanBuilder().expand_implementations(
                module_expansion.plan,
                blueprint,
                [valid],
                blueprint_work_item_id="wi-blueprint",
                module_work_item_ids={"catalog": "wi-architecture-module-catalog"},
                integration_work_item_id="wi-architecture-integration",
            )
        wrong = valid.model_copy(update={"purpose": "其他价值"})
        with self.assertRaises(BlueprintValidationError):
            DynamicPlanBuilder().expand_implementations(
                module_expansion.plan,
                blueprint,
                [wrong, ModuleDesign(
                    schema_version=1,
                    design_id="module-orders",
                    parent_design_id=blueprint.design_id,
                    module_id="orders",
                    purpose="完成购买",
                    responsibilities=["orders"],
                    depends_on_modules=["catalog"],
                )],
                blueprint_work_item_id="wi-blueprint",
                module_work_item_ids={
                    "catalog": "wi-architecture-module-catalog",
                    "orders": "wi-architecture-module-orders",
                },
                integration_work_item_id="wi-architecture-integration",
            )

    def test_expansion_provenance_and_stage_id_survive_plan_persistence(self) -> None:
        blueprint = _blueprint(ModuleRef(module_id="api", responsibility="接口"))
        expansion = DynamicPlanBuilder().expand_modules(
            _base_plan(False), blueprint, blueprint_work_item_id="wi-blueprint"
        )
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace(expansion.plan.goal)
            persisted_plan = replace(expansion.plan, trace=trace)
            persisted_expansion = replace(expansion, plan=persisted_plan)
            traces.record_plan(persisted_plan)
            traces.record_plan_expansion(persisted_expansion.as_dict())
            loaded = traces.load_plan(trace.trace_id)
            self.assertEqual(
                loaded.work_item("wi-architecture-module-api").stage_id,
                "architecture_module",
            )
            self.assertEqual(
                traces.load_plan_expansion(trace.trace_id, persisted_plan.id)[
                    "added_work_item_ids"
                ],
                ["wi-architecture-module-api"],
            )
            self.assertEqual(
                len(traces.load_plan_expansion(trace.trace_id, persisted_plan.id)["blueprint_digest"]),
                64,
            )

    def test_runner_expands_completed_blueprint_before_next_ready_step(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"),
            ModuleRef(module_id="orders", responsibility="订单", purpose="完成购买", depends_on_modules=["catalog"]),
        )
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("构建电商系统")
            plan = replace(_base_plan(), trace=trace)
            traces.record_plan(plan)
            ArchitectureArtifactWorkflow(project_path).write_staged_design(
                ExecutionContext(
                    trace_id=trace.trace_id,
                    work_item_id="wi-blueprint",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.PARTITIONED,
                    slot="blueprint",
                ),
                blueprint.model_dump(mode="json"),
            )
            state = RunState(plan=plan)
            state.record(
                plan.work_item("wi-blueprint"),
                NodeResult.completed(
                    work_item_id="wi-blueprint",
                    agent_id="architecture_agent",
                    content="staged blueprint",
                ),
            )
            runner = GraphRunner(AgentRegistry(), ToolGateway(), traces=traces)
            self.assertIsNone(runner._expand_architecture_modules_if_ready(state))
            self.assertEqual(
                {item.stage_id for item in state.plan.work_items if item.stage_id == "architecture_module"},
                {"architecture_module"},
            )
            integration = state.plan.work_item("wi-architecture-integration")
            self.assertIn("wi-architecture-module-catalog", integration.dependency_ids)

    def test_runner_does_not_finish_before_expanding_blueprint_only_plan(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"),
            ModuleRef(module_id="orders", responsibility="订单", purpose="完成购买", depends_on_modules=["catalog"]),
        )

        class DynamicAgent:
            def run(self, task, *, context=None):
                workflow = ArchitectureArtifactWorkflow(project_path)
                if context.execution_mode is ExecutionMode.INTEGRATION:
                    workflow.integrate_structured_designs(context)
                elif context.slot == "blueprint":
                    design = blueprint
                else:
                    module_id = context.slot.removeprefix("module-")
                    design = ModuleDesign(
                        schema_version=1,
                        design_id=f"module-{module_id}",
                        parent_design_id=blueprint.design_id,
                        module_id=module_id,
                        purpose=next(item.purpose for item in blueprint.modules if item.module_id == module_id),
                        responsibilities=[module_id],
                    )
                workflow.write_staged_design(context, design.model_dump(mode="json"))
                return AgentResult.completed("saved")

        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("动态模块运行")
            plan = replace(_base_plan(False), trace=trace)
            plan = replace(
                plan,
                work_items=(replace(plan.work_items[0], input_refs=(), contract_digest=None),),
            )
            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    id="architecture_agent",
                    domain="architecture",
                    description="架构",
                    output_key="architecture",
                    max_parallel_instances=4,
                ),
                factory=lambda: DynamicAgent(),
            )
            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
                max_workers=4,
            ).run(plan)
            self.assertEqual(result.status.value, "completed", result.error)
            self.assertIn("wi-architecture-module-catalog", result.state.node_results)
            self.assertEqual(len(result.state.plan.work_items), 3)

    def test_runner_executes_dynamic_implementation_and_architecture_integration(self) -> None:
        blueprint = _blueprint(
            ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"),
            ModuleRef(module_id="orders", responsibility="订单", purpose="完成购买", depends_on_modules=["catalog"]),
        )

        class DynamicAgent:
            def run(self, task, *, context=None):
                workflow = ArchitectureArtifactWorkflow(project_path)
                if context.execution_mode is ExecutionMode.INTEGRATION:
                    workflow.integrate_structured_designs(context)
                elif context.slot == "blueprint":
                    workflow.write_staged_design(context, blueprint.model_dump(mode="json"))
                elif context.slot.startswith("module-"):
                    module_id = context.slot.removeprefix("module-")
                    module = next(item for item in blueprint.modules if item.module_id == module_id)
                    design = ModuleDesign(
                        schema_version=1,
                        design_id=f"module-{module_id}",
                        parent_design_id=blueprint.design_id,
                        module_id=module_id,
                        purpose=module.purpose,
                        responsibilities=[module.responsibility],
                        depends_on_modules=module.depends_on_modules,
                    )
                    workflow.write_staged_design(context, design.model_dump(mode="json"))
                elif context.slot.startswith("implementation-"):
                    module_id = context.slot.removeprefix("implementation-")
                    design = ImplementationDesign(
                        schema_version=1,
                        design_id=f"implementation-{module_id}",
                        parent_design_id=f"module-{module_id}",
                        module_id=module_id,
                        implementation_units=[
                            {
                                "unit_id": f"unit-{module_id}",
                                "layer": module_id,
                                "objective": f"实现 {module_id}",
                                "allowed_paths": [f"backend/app/{module_id}/**"],
                                "owned_files": [f"backend/app/{module_id}/main.py"],
                            }
                        ],
                    )
                    workflow.write_staged_design(context, design.model_dump(mode="json"))
                return AgentResult.completed("saved")

        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("动态架构三层运行")
            plan = replace(_base_plan(), trace=trace)
            plan = replace(
                plan,
                work_items=(replace(plan.work_items[0], input_refs=(), contract_digest=None), *plan.work_items[1:]),
            )
            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    id="architecture_agent",
                    domain="architecture",
                    description="架构",
                    output_key="architecture",
                    max_parallel_instances=4,
                ),
                factory=lambda: DynamicAgent(),
            )
            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
                max_workers=4,
            ).run(plan)
            self.assertEqual(result.status.value, "completed", result.error)
            self.assertIn("wi-architecture-implementation-catalog", result.state.node_results)
            self.assertIn("wi-architecture-implementation-orders", result.state.node_results)
            self.assertIn("wi-architecture-integration", result.state.node_results)
            self.assertEqual(len(result.state.plan.work_items), 6)
            events = traces.list_events(trace.trace_id)
            self.assertTrue(any(event.get("type") == "architecture_implementations_expanded" for event in events))
            latest = traces.load_plan_expansion(trace.trace_id, plan.id)
            self.assertEqual(latest["kind"], "architecture_implementation_expansion")

            # Overall phase-3 hand-off: the integrated three-layer objects are
            # compiled into the single Project Contract, then deterministically
            # expanded into file-level CodeAgent WorkItems.
            integration = result.state.plan.work_item("wi-architecture-integration")
            contract_context = ExecutionContext(
                trace_id=trace.trace_id,
                work_item_id=integration.id,
                agent_id="architecture_agent",
                execution_mode=ExecutionMode.INTEGRATION,
                input_refs=integration.input_refs,
                publish_target="architecture_contract",
            )
            ArchitectureArtifactWorkflow(project_path).compile_project_contract_from_designs(
                contract_context
            )
            from app.domain.architecture.implementation_contract import ProjectContractStore
            from app.workflow.compiler import ImplementationContractCompiler

            contract = ProjectContractStore(project_path).load()
            code_plan = ImplementationContractCompiler().compile(
                contract,
                goal=plan.goal,
                plan_id="implementation-plan",
                trace=trace,
            )
            self.assertEqual(
                {item.implementation_unit_id for item in code_plan.work_items},
                {"unit-catalog", "unit-orders"},
            )
            self.assertTrue(all(len(item.owned_files) == 1 for item in code_plan.work_items))

    def test_planner_stage_id_compiles_controlled_blueprint_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            artifacts = ArtifactStore(project_path)
            artifacts.save("requirement", "# 需求")
            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    id="architecture_agent",
                    domain="architecture",
                    description="架构",
                    output_key="architecture",
                ),
                factory=lambda: None,
            )
            context = PlanningContext.build(
                goal="设计一个动态模块系统",
                agents=agents,
                templates=WorkflowTemplateRegistry(),
                artifacts=artifacts,
            )
            plan = PlanValidator().validate(
                PlanDraft.model_validate(
                    {
                        "rationale": "先产出蓝图",
                        "steps": [
                            {
                                "ref": "blueprint",
                                "agent_id": "architecture_agent",
                                "stage_id": "architecture_blueprint",
                                "objective": "产出总体蓝图",
                            }
                        ],
                    }
                ),
                context=context,
                plan_id="dynamic-stage",
                trace=TraceContext(requirement_id="req", trace_id="tr-stage"),
            )
            item = plan.work_items[0]
            self.assertEqual(item.execution_mode, ExecutionMode.PARTITIONED)
            self.assertEqual(item.slot, "blueprint")
            self.assertEqual(item.output_kind, "ArchitectureBlueprint")


if __name__ == "__main__":
    unittest.main()

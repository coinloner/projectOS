import json
import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    ArchitectureDesignBundle,
    ImplementationDesign,
    LayerDecision,
    ModuleDesign,
    ModuleRef,
    parse_design,
)
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.domain.architecture.service import implementation_design_budget, implementation_unit_budget
from app.domain.architecture.service import _normalize_unit_waves
from app.domain.architecture.contract_input import (
    ConsumedInterfaceRefInput,
    ContractEntrypointInput,
    ProjectContractInput,
)
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.trace import TraceContext
from app.workflow.compiler import TemplateCompiler
from app.workflow.templates import architecture_layered_template


def _blueprint() -> ArchitectureBlueprint:
    return ArchitectureBlueprint(
        schema_version=1,
        design_id="blueprint-1",
        system_boundary="订单系统负责下单和订单状态查询。",
        layers=[
            LayerDecision(
                name="domain",
                allowed_dependencies=[],
                path_mapping=["backend/app/domain/**"],
            ),
            LayerDecision(
                name="api",
                allowed_dependencies=["domain"],
                path_mapping=["backend/app/api/**"],
            ),
        ],
        modules=[
            ModuleRef(module_id="domain", responsibility="订单规则"),
            ModuleRef(module_id="api", responsibility="HTTP 接口"),
        ],
    )


def _module(module_id: str) -> ModuleDesign:
    return ModuleDesign(
        schema_version=1,
        design_id=f"module-{module_id}",
        parent_design_id="blueprint-1",
        module_id=module_id,
        responsibilities=[f"{module_id} 模块职责"],
    )


def _implementation(module_id: str) -> ImplementationDesign:
    unit_id = f"unit-{module_id}"
    return ImplementationDesign(
        schema_version=1,
        design_id=f"implementation-{module_id}",
        parent_design_id=f"module-{module_id}",
        module_id=module_id,
        implementation_units=[
            {
                "unit_id": unit_id,
                "layer": module_id,
                "objective": f"实现 {module_id}",
                "allowed_paths": [f"backend/app/{module_id}/**"],
                "required_files": [f"backend/app/{module_id}/main.py"],
                "owned_files": [f"backend/app/{module_id}/main.py"],
                "acceptance_criteria": ["文件可导入"],
            }
        ],
    )


class ArchitectureDesignContractTest(unittest.TestCase):
    def test_blueprint_required_file_must_have_implementation_unit_owner(self) -> None:
        blueprint = _blueprint().model_copy(
            update={"required_files": ["backend/Dockerfile"]}
        )
        with self.assertRaisesRegex(
            ValueError,
            "ArchitectureBlueprint.required_file_not_owned: backend/Dockerfile",
        ):
            ArchitectureDesignBundle(
                schema_version=1,
                blueprint=blueprint,
                modules=[_module("domain"), _module("api")],
                implementations=[_implementation("domain"), _implementation("api")],
            )

    def test_blueprint_required_file_can_be_owned_by_any_implementation_unit(self) -> None:
        blueprint = _blueprint().model_copy(
            update={"required_files": ["backend/app/api/main.py"]}
        )
        bundle = ArchitectureDesignBundle(
            schema_version=1,
            blueprint=blueprint,
            modules=[_module("domain"), _module("api")],
            implementations=[_implementation("domain"), _implementation("api")],
        )

        self.assertEqual(bundle.blueprint.required_files, ["backend/app/api/main.py"])

    def test_contract_derives_unique_runtime_entrypoint_when_blueprint_omits_it(self) -> None:
        runtime = ImplementationDesign(
            schema_version=1,
            design_id="implementation-runtime",
            parent_design_id="module-api",
            module_id="api",
            implementation_units=[
                {
                    "unit_id": "runtime-server",
                    "layer": "runtime",
                    "objective": "实现服务入口",
                    "allowed_paths": ["backend/runtime/**"],
                    "owned_files": ["backend/runtime/server.py"],
                }
            ],
        )
        contract = ArchitectureDesignBundle(
            schema_version=1,
            blueprint=_blueprint(),
            modules=[_module("domain"), _module("api")],
            implementations=[_implementation("domain"), runtime],
        ).to_project_contract()
        self.assertEqual(
            contract["entrypoints"],
            {
                "backend_file": "backend/runtime/server.py",
                "backend_import": "runtime.server",
                "backend_command": "python -m runtime.server",
                "health_path": "/health",
            },
        )

    def test_consumed_interface_wire_aliases_are_canonicalized(self) -> None:
        module = ModuleDesign.model_validate(
            {
                "schema_version": 1,
                "design_id": "module-api",
                "parent_design_id": "blueprint-1",
                "module_id": "api",
                "responsibilities": ["HTTP 接口"],
                "consumed_interfaces": [
                    {
                        "interface_id": "inventory.stock",
                        "direction": "consumed",
                        "summary": "调用库存查询",
                        "usage": "调用库存查询",
                        "required": True,
                    }
                ],
            }
        )
        self.assertEqual(
            module.consumed_interfaces[0].model_dump(),
            {
                "interface_id": "inventory.stock",
                "direction": "consumed",
                "summary": "调用库存查询",
            },
        )

    def test_contract_consumed_interface_legacy_fields_are_canonicalized(self) -> None:
        value = ConsumedInterfaceRefInput.model_validate(
            {
                "interface_id": "inventory.stock",
                "direction": "consumed",
                "summary": "调用库存查询",
                "required": True,
            }
        )
        self.assertEqual(value.usage, "调用库存查询")
        self.assertTrue(value.required)
        self.assertNotIn("direction", value.model_dump())

    def test_contract_entrypoint_health_path_null_is_normalized(self) -> None:
        base = {
            "schema_version": 1,
            "layers": [{"name": "api"}],
            "required_test_types": [],
            "entrypoints": {
                "backend_file": "backend/app/main.py",
                "health_path": None,
            },
            "required_files": [],
            "interfaces": [],
            "implementation_units": [
                {
                    "unit_id": "unit-api",
                    "layer": "api",
                    "objective": "实现 API",
                    "allowed_paths": ["backend/app/**"],
                }
            ],
        }

        normalized = ProjectContractInput.model_validate(base)

        self.assertEqual(normalized.entrypoints.health_path, "/health")
        self.assertEqual(
            normalized.to_canonical_dict()["entrypoints"]["health_path"],
            "/health",
        )

    def test_contract_entrypoint_empty_health_path_uses_default(self) -> None:
        value = ContractEntrypointInput.model_validate({"health_path": "  "})

        self.assertEqual(value.health_path, "/health")
    def test_bundle_enforces_three_level_parent_semantics(self) -> None:
        bundle = ArchitectureDesignBundle(
            schema_version=1,
            blueprint=_blueprint(),
            modules=[_module("domain"), _module("api")],
            implementations=[_implementation("domain"), _implementation("api")],
        )
        self.assertEqual(bundle.blueprint.depth, 0)
        self.assertEqual({item.depth for item in bundle.modules}, {1})
        self.assertEqual({item.depth for item in bundle.implementations}, {2})
        contract = bundle.to_project_contract()
        self.assertEqual(len(contract["implementation_units"]), 2)
        normalized = ProjectContractInput.model_validate(contract)
        self.assertEqual(len(normalized.implementation_units), 2)

    def test_design_parser_rejects_depth_above_two(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能是 0、1 或 2"):
            parse_design({"schema_version": 1, "depth": 3})

    def test_implementation_design_requires_single_owned_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能负责一个"):
            ImplementationDesign(
                schema_version=1,
                design_id="implementation-domain",
                parent_design_id="module-domain",
                module_id="domain",
                implementation_units=[
                    {
                        "unit_id": "unit-domain",
                        "layer": "domain",
                        "objective": "实现领域",
                        "allowed_paths": ["backend/app/domain/**"],
                        "owned_files": [
                            "backend/app/domain/a.py",
                            "backend/app/domain/b.py",
                        ],
                    }
                ],
            )

    def test_implementation_design_separates_provided_and_consumed_interfaces(self) -> None:
        design = ImplementationDesign(
            schema_version=1,
            design_id="implementation-api",
            parent_design_id="module-api",
            module_id="api",
            provided_interfaces=[
                {
                    "interface_id": "api.http",
                    "kind": "api",
                    "name": "HTTP API",
                    "owner_unit": "unit-api",
                }
            ],
            consumed_interfaces=[
                {"interface_id": "domain.todo", "usage": "调用领域服务"}
            ],
            implementation_units=[
                {
                    "unit_id": "unit-api",
                    "layer": "api",
                    "objective": "实现 API",
                    "allowed_paths": ["backend/app/api/**"],
                    "owned_files": ["backend/app/api/main.py"],
                }
            ],
        )
        self.assertEqual(design.provided_interfaces[0].owner_unit, "unit-api")
        self.assertEqual(design.consumed_interfaces[0].interface_id, "domain.todo")
        self.assertNotIn("interfaces", design.model_dump())

    def test_legacy_mixed_interfaces_are_migrated_at_wire_boundary(self) -> None:
        design = ImplementationDesign.model_validate(
            {
                "schema_version": 1,
                "design_id": "implementation-api",
                "parent_design_id": "module-api",
                "module_id": "api",
                "interfaces": [
                    {
                        "interface_id": "api.http",
                        "kind": "api",
                        "name": "HTTP API",
                        "owner_unit": "unit-api",
                    },
                    {
                        "interface_id": "domain.todo",
                        "kind": "internal-consumed",
                        "name": "Todo service",
                        "owner_unit": "unit-api",
                    },
                ],
                "implementation_units": [
                    {
                        "unit_id": "unit-api",
                        "layer": "api",
                        "objective": "实现 API",
                        "allowed_paths": ["backend/app/api/**"],
                        "owned_files": ["backend/app/api/main.py"],
                    }
                ],
            }
        )
        self.assertEqual([i.interface_id for i in design.provided_interfaces], ["api.http"])
        self.assertEqual([i.interface_id for i in design.consumed_interfaces], ["domain.todo"])

    def test_consumed_interface_must_reference_a_provider(self) -> None:
        provider = _implementation("domain")
        consumer = ImplementationDesign(
            schema_version=1,
            design_id="implementation-api",
            parent_design_id="module-api",
            module_id="api",
            consumed_interfaces=[{"interface_id": "missing.service"}],
            implementation_units=[
                {
                    "unit_id": "unit-api",
                    "layer": "api",
                    "objective": "实现 API",
                    "allowed_paths": ["backend/app/api/**"],
                    "owned_files": ["backend/app/api/main.py"],
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "未声明的接口"):
            ArchitectureDesignBundle(
                schema_version=1,
                blueprint=_blueprint(),
                modules=[_module("domain"), _module("api")],
                implementations=[provider, consumer],
            )

    def test_layered_template_has_l0_l1_l2_and_integration_barrier(self) -> None:
        trace = TraceContext(requirement_id="req", trace_id="tr-layered")
        plan = TemplateCompiler().compile(
            architecture_layered_template(),
            goal="设计订单系统",
            plan_id="plan-layered",
            trace=trace,
            agent_output_keys={
                "architecture_agent": "architecture",
                "architecture_contract_agent": "architecture_contract",
            },
        )
        self.assertEqual(len(plan.work_items), 10)
        self.assertEqual(plan.work_items[0].slot, "blueprint")
        self.assertEqual(
            {item.slot for item in plan.work_items[1:4]},
            {"module-domain", "module-api", "module-runtime"},
        )
        integration = plan.work_items[-3]
        self.assertEqual(integration.execution_mode, ExecutionMode.INTEGRATION)
        self.assertEqual(len(integration.input_refs), 7)
        contract = plan.work_items[-1]
        self.assertEqual(contract.agent_id, "architecture_contract_agent")
        self.assertEqual(contract.execution_mode, ExecutionMode.INTEGRATION)
        self.assertEqual(len(contract.input_refs), 7)

    def test_workflow_writes_and_integrates_structured_objects(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace_id = "tr-design"
            for item, design in (
                ("blueprint", _blueprint().model_dump(mode="json")),
                ("module-domain", _module("domain").model_dump(mode="json")),
                ("module-api", _module("api").model_dump(mode="json")),
                ("implementation-domain", _implementation("domain").model_dump(mode="json")),
                ("implementation-api", _implementation("api").model_dump(mode="json")),
            ):
                context = ExecutionContext(
                    trace_id=trace_id,
                    work_item_id=f"wi-{item}",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.PARTITIONED,
                    slot=item,
                )
                workflow.write_staged_design(context, design)

            repository = ArtifactRepository(project_path)
            refs = tuple(
                ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=trace_id,
                    work_item_id=f"wi-{item}",
                    slot=item,
                )
                for item in (
                    "blueprint",
                    "module-domain",
                    "module-api",
                    "implementation-domain",
                    "implementation-api",
                )
            )
            integration_context = ExecutionContext(
                trace_id=trace_id,
                work_item_id="wi-integration",
                agent_id="architecture_agent",
                execution_mode=ExecutionMode.INTEGRATION,
                input_refs=refs,
                publish_target="architecture",
            )
            result = workflow.integrate_structured_designs(integration_context)
            self.assertIn("structured_designs=5", result)
            retry_result = workflow.integrate_structured_designs(integration_context)
            self.assertIn("复用架构候选", retry_result)
            candidate = repository.candidate_for_work_item(
                trace_id=trace_id,
                artifact_key="architecture",
                work_item_id="wi-integration",
            )
            self.assertIn("# Architecture", candidate.content)
            contract_result = workflow.compile_project_contract_from_designs(integration_context)
            self.assertIn('"ok": true', contract_result)
            self.assertTrue(
                (Path(project_path) / ".projectos" / "architecture" / "project-contract.json").is_file()
            )

    def test_integration_normalizes_qualified_blueprint_module_ids(self) -> None:
        """Layer workers may use ``api`` while blueprints use ``todo-api``."""
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace_id = "tr-qualified-modules"
            blueprint = _blueprint().model_copy(update={
                "modules": [
                    ModuleRef(module_id="todo-domain", responsibility="订单规则"),
                    ModuleRef(module_id="todo-api", responsibility="HTTP 接口"),
                ]
            })
            designs = (
                ("blueprint", blueprint.model_dump(mode="json")),
                ("module-domain", _module("domain").model_dump(mode="json")),
                ("module-api", _module("api").model_dump(mode="json")),
                ("implementation-domain", _implementation("domain").model_dump(mode="json")),
                ("implementation-api", _implementation("api").model_dump(mode="json")),
            )
            for slot, design in designs:
                workflow.write_staged_design(
                    ExecutionContext(
                        trace_id=trace_id,
                        work_item_id=f"wi-{slot}",
                        agent_id="architecture_agent",
                        execution_mode=ExecutionMode.PARTITIONED,
                        slot=slot,
                    ),
                    design,
                )
            refs = tuple(
                ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=trace_id,
                    work_item_id=f"wi-{slot}",
                    slot=slot,
                )
                for slot, _ in designs
            )
            result = workflow.integrate_structured_designs(
                ExecutionContext(
                    trace_id=trace_id,
                    work_item_id="wi-integration",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.INTEGRATION,
                    input_refs=refs,
                    publish_target="architecture",
                )
            )
            self.assertIn("structured_designs=5", result)

    def test_integration_normalizes_unambiguous_interface_id_alias(self) -> None:
        """A shortened consumer id is mapped to the ModuleDesign catalogue."""
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace_id = "tr-interface-alias"
            domain_module = ModuleDesign.model_validate({
                **_module("domain").model_dump(mode="json"),
                "provided_interfaces": [
                    {
                        "interface_id": "domain.todo_task_operations",
                        "direction": "provided",
                        "summary": "Todo 领域操作",
                    }
                ],
            })
            api_module = ModuleDesign.model_validate({
                **_module("api").model_dump(mode="json"),
                "consumed_interfaces": [
                    {
                        "interface_id": "domain.todo_operations",
                        "direction": "consumed",
                        "summary": "调用 Todo 领域操作",
                    }
                ],
            })
            domain_impl = ImplementationDesign.model_validate({
                **_implementation("domain").model_dump(mode="json"),
                "provided_interfaces": [
                    {
                        "interface_id": "domain.todo_task_operations",
                        "kind": "internal",
                        "name": "Todo 领域操作",
                        "owner_unit": "unit-domain",
                    }
                ],
            })
            api_impl = ImplementationDesign.model_validate({
                **_implementation("api").model_dump(mode="json"),
                "consumed_interfaces": [
                    {
                        "interface_id": "domain.todo_operations",
                        "usage": "调用 Todo 领域操作",
                    }
                ],
            })
            designs = (
                ("blueprint", _blueprint().model_dump(mode="json")),
                ("module-domain", domain_module.model_dump(mode="json")),
                ("module-api", api_module.model_dump(mode="json")),
                ("implementation-domain", domain_impl.model_dump(mode="json")),
                ("implementation-api", api_impl.model_dump(mode="json")),
            )
            for slot, design in designs:
                workflow.write_staged_design(
                    ExecutionContext(
                        trace_id=trace_id,
                        work_item_id=f"wi-{slot}",
                        agent_id="architecture_agent",
                        execution_mode=ExecutionMode.PARTITIONED,
                        slot=slot,
                    ),
                    design,
                )
            refs = tuple(
                ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=trace_id,
                    work_item_id=f"wi-{slot}",
                    slot=slot,
                )
                for slot, _ in designs
            )
            result = workflow.integrate_structured_designs(
                ExecutionContext(
                    trace_id=trace_id,
                    work_item_id="wi-integration",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.INTEGRATION,
                    input_refs=refs,
                    publish_target="architecture",
                )
            )
            self.assertIn("structured_designs=5", result)

    def test_integration_dedupes_consumed_references_converging_after_alias_normalization(self) -> None:
        """Two aliases for one declared interface must remain one contract edge."""
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace_id = "tr-interface-alias-dedup"
            domain_module = ModuleDesign.model_validate({
                **_module("domain").model_dump(mode="json"),
                "provided_interfaces": [
                    {
                        "interface_id": "domain.todo_task_operations",
                        "direction": "provided",
                        "summary": "Todo 领域操作",
                    }
                ],
            })
            api_impl = ImplementationDesign.model_validate({
                **_implementation("api").model_dump(mode="json"),
                "consumed_interfaces": [
                    {
                        "interface_id": "domain.todo_operations",
                        "usage": "读取待办事项",
                    },
                    {
                        "interface_id": "domain.todo_task_operations",
                        "usage": "执行待办事项操作",
                    },
                ],
            })
            domain_impl = ImplementationDesign.model_validate({
                **_implementation("domain").model_dump(mode="json"),
                "provided_interfaces": [
                    {
                        "interface_id": "domain.todo_task_operations",
                        "kind": "internal",
                        "name": "Todo 领域操作",
                        "owner_unit": "unit-domain",
                    }
                ],
            })
            designs = (
                ("blueprint", _blueprint().model_dump(mode="json")),
                ("module-domain", domain_module.model_dump(mode="json")),
                ("module-api", _module("api").model_dump(mode="json")),
                ("implementation-domain", domain_impl.model_dump(mode="json")),
                ("implementation-api", api_impl.model_dump(mode="json")),
            )
            for slot, design in designs:
                workflow.write_staged_design(
                    ExecutionContext(
                        trace_id=trace_id,
                        work_item_id=f"wi-{slot}",
                        agent_id="architecture_agent",
                        execution_mode=ExecutionMode.PARTITIONED,
                        slot=slot,
                    ),
                    design,
                )
            refs = tuple(
                ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=trace_id,
                    work_item_id=f"wi-{slot}",
                    slot=slot,
                )
                for slot, _ in designs
            )
            bundle = workflow._load_design_bundle(
                ExecutionContext(
                    trace_id=trace_id,
                    work_item_id="wi-integration",
                    agent_id="architecture_agent",
                    execution_mode=ExecutionMode.INTEGRATION,
                    input_refs=refs,
                    publish_target="architecture",
                )
            )
            api_design = next(item for item in bundle.implementations if item.module_id == "api")
            self.assertEqual(
                [reference.interface_id for reference in api_design.consumed_interfaces],
                ["domain.todo_task_operations"],
            )
            self.assertEqual(api_design.consumed_interfaces[0].usage, "读取待办事项")

    def test_workflow_normalizes_multi_file_implementation_unit(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            context = ExecutionContext(
                trace_id="tr-normalize",
                work_item_id="wi-implementation-domain",
                agent_id="architecture_agent",
                execution_mode=ExecutionMode.PARTITIONED,
                slot="implementation-domain",
            )
            design = _implementation("domain").model_dump(mode="json")
            design["implementation_units"][0]["owned_files"] = [
                "backend/app/domain/models.py",
                "backend/app/domain/services.py",
            ]
            workflow.write_staged_design(context, design)
            stored = json.loads(
                (Path(project_path) / ".projectos" / "runs" / "tr-normalize"
                 / "work-items" / "wi-implementation-domain" / "output"
                 / "implementation-domain.md").read_text()
            )
            self.assertEqual(
                [unit["owned_files"] for unit in stored["implementation_units"]],
                [["backend/app/domain/models.py"], ["backend/app/domain/services.py"]],
            )

    def test_design_validation_error_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            context = ExecutionContext(
                trace_id="tr-invalid-design",
                work_item_id="wi-implementation-domain",
                agent_id="architecture_agent",
                execution_mode=ExecutionMode.PARTITIONED,
                slot="implementation-domain",
            )
            design = _implementation("domain").model_dump(mode="json")
            unit = design["implementation_units"][0]
            unit["required_files"] = ["backend/app/domain/other.py"]
            unit["required_paths"] = ["backend/app/domain/main.py"]
            with self.assertRaises(ValueError) as raised:
                workflow.write_staged_design(context, design)
            payload = json.loads(str(raised.exception))
            self.assertEqual(payload["error_type"], "design_validation")
            self.assertTrue(payload["retryable"])

    def test_implementation_design_rejects_required_path_owned_by_another_unit(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "实现单元 domain-inventory-service 的 required_paths "
            "必须属于 owned_files: inventory_service/domain/models.py",
        ):
            ImplementationDesign(
                schema_version=1,
                design_id="implementation-domain",
                parent_design_id="module-domain",
                module_id="domain",
                implementation_units=[
                    {
                        "unit_id": "domain-models-errors",
                        "layer": "domain",
                        "objective": "定义库存模型和领域错误",
                        "allowed_paths": ["inventory_service/domain/**"],
                        "owned_files": ["inventory_service/domain/models.py"],
                    },
                    {
                        "unit_id": "domain-inventory-service",
                        "layer": "domain",
                        "objective": "实现库存领域服务",
                        "allowed_paths": ["inventory_service/domain/**"],
                        "owned_files": ["inventory_service/domain/service.py"],
                        "required_paths": ["inventory_service/domain/models.py"],
                        "depends_on": ["domain-models-errors"],
                    },
                ],
            )

    def test_prefixed_implementation_slot_uses_implementation_limit(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            workflow = ArchitectureArtifactWorkflow(project_path)
            context = ExecutionContext(
                trace_id="tr-large-design",
                work_item_id="wi-implementation-api",
                agent_id="architecture_agent",
                execution_mode=ExecutionMode.PARTITIONED,
                slot="implementation-api",
            )
            design = _implementation("api").model_dump(mode="json")
            design["required_test_types"] = ["x" * 1000 for _ in range(7)]
            with self.assertRaises(ValueError) as raised:
                workflow.write_staged_design(context, design)
            payload = json.loads(str(raised.exception))
            self.assertEqual(payload["error_type"], "artifact_budget")
            self.assertEqual(payload["limit"], 6000)

    def test_implementation_budget_scales_after_third_unit(self) -> None:
        self.assertEqual(implementation_design_budget(1), 6000)
        self.assertEqual(implementation_design_budget(2), 9500)
        self.assertEqual(implementation_design_budget(3), 13000)
        self.assertEqual(implementation_design_budget(4), 16500)
        self.assertEqual(implementation_design_budget(10), 24000)
        self.assertEqual(implementation_unit_budget(), 4500)

    def test_integration_normalizes_dependent_units_to_later_waves(self) -> None:
        design = ImplementationDesign(
            schema_version=1,
            design_id="implementation-domain",
            parent_design_id="module-domain",
            module_id="domain",
            implementation_units=[
                {
                    "unit_id": "domain-store",
                    "layer": "domain",
                    "objective": "持久化任务",
                    "allowed_paths": ["backend/app/domain/**"],
                    "owned_files": ["backend/app/domain/store.py"],
                    "wave": 1,
                    "depends_on": ["domain-entity"],
                },
                {
                    "unit_id": "domain-entity",
                    "layer": "domain",
                    "objective": "定义任务实体",
                    "allowed_paths": ["backend/app/domain/**"],
                    "owned_files": ["backend/app/domain/entities.py"],
                    "wave": 1,
                },
            ],
        )
        normalized = _normalize_unit_waves([design])[0]
        waves = {unit.unit_id: unit.wave for unit in normalized.implementation_units}
        self.assertEqual(waves, {"domain-entity": 1, "domain-store": 2})

    def test_token_budget_tracks_unit_hint(self) -> None:
        from app.domain.architecture.service import llm_token_budget_for_design

        self.assertEqual(llm_token_budget_for_design("implementation-api", unit_count=1), 9000)
        self.assertEqual(llm_token_budget_for_design("implementation-api", unit_count=10), 24000)
        self.assertIsNone(llm_token_budget_for_design("module-api", unit_count=10))

    def test_wire_aliases_are_normalized_once(self) -> None:
        from app.domain.architecture.contract_input import ContractImplementationUnitInput

        value = ContractImplementationUnitInput.model_validate({
            "unit_id": "u-api",
            "layer": "api",
            "objective": "实现接口",
            "allowed_paths": ["backend/api/**"],
            "required_files": ["backend/api/main.py"],
            "output_slot": "backend",
            "provides": ["api.orders"],
            "consumes": ["application.orders"],
        })
        self.assertEqual(value.required_paths, ["backend/api/main.py"])
        self.assertFalse(hasattr(value, "required_files") and value.required_files)
        self.assertEqual(value.slot, "backend")
        self.assertEqual(value.provides_interfaces, ["api.orders"])
        self.assertEqual(value.consumes_interfaces, ["application.orders"])

    def test_wire_alias_conflict_is_rejected(self) -> None:
        from app.domain.architecture.contract_input import ContractImplementationUnitInput

        with self.assertRaises(ValueError):
            ContractImplementationUnitInput.model_validate({
                "unit_id": "u-api",
                "layer": "api",
                "objective": "实现接口",
                "allowed_paths": ["backend/api/**"],
                "required_files": ["backend/api/main.py"],
                "required_paths": ["backend/api/other.py"],
            })


class BlueprintLayerDependencyValidationTest(unittest.TestCase):
    """测试层依赖的引用完整性校验在 pydantic 模型层就能拦住。"""

    def test_layer_dependency_must_reference_declared_layers(self) -> None:
        """层的 allowed_dependencies 必须引用已声明的层,否则 pydantic 解析失败。"""
        with self.assertRaises(ValueError) as cm:
            ArchitectureBlueprint.model_validate({
                "schema_version": 1,
                "design_id": "test-blueprint",
                "system_boundary": "测试系统",
                "layers": [
                    {
                        "name": "presentation",
                        "allowed_dependencies": ["application", "Python 标准库"],
                        "path_mapping": ["cli/**"],
                    },
                    {
                        "name": "application",
                        "allowed_dependencies": ["data"],
                        "path_mapping": ["core/**"],
                    },
                    {
                        "name": "data",
                        "allowed_dependencies": [],
                        "path_mapping": ["storage/**"],
                    },
                ],
                "modules": [
                    {"module_id": "cli", "responsibility": "命令行"},
                ],
            })
        error_message = str(cm.exception)
        self.assertIn("allowed_dependencies", error_message.lower())
        self.assertIn("Python 标准库", error_message)

    def test_layer_dependency_valid_when_all_references_exist(self) -> None:
        """所有层依赖都引用已声明的层时,解析成功。"""
        blueprint = ArchitectureBlueprint.model_validate({
            "schema_version": 1,
            "design_id": "test-blueprint",
            "system_boundary": "测试系统",
            "layers": [
                {
                    "name": "presentation",
                    "allowed_dependencies": ["application"],
                    "path_mapping": ["cli/**"],
                },
                {
                    "name": "application",
                    "allowed_dependencies": ["data"],
                    "path_mapping": ["core/**"],
                },
                {
                    "name": "data",
                    "allowed_dependencies": [],
                    "path_mapping": ["storage/**"],
                },
            ],
            "modules": [
                {"module_id": "cli", "responsibility": "命令行"},
            ],
        })
        self.assertEqual(blueprint.design_id, "test-blueprint")
        self.assertEqual(len(blueprint.layers), 3)

    def test_layer_self_dependency_rejected(self) -> None:
        """层不能依赖自己。"""
        with self.assertRaises(ValueError) as cm:
            ArchitectureBlueprint.model_validate({
                "schema_version": 1,
                "design_id": "test-blueprint",
                "system_boundary": "测试系统",
                "layers": [
                    {
                        "name": "application",
                        "allowed_dependencies": ["application"],
                        "path_mapping": ["app/**"],
                    },
                ],
                "modules": [
                    {"module_id": "app", "responsibility": "应用"},
                ],
            })
        error_message = str(cm.exception)
        self.assertIn("application", error_message)
        self.assertIn("自身", error_message.lower())


if __name__ == "__main__":
    unittest.main()

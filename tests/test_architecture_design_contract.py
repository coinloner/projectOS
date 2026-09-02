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
from app.domain.architecture.contract_input import ProjectContractInput
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
            self.assertEqual(payload["limit"], 5000)

    def test_implementation_budget_scales_after_third_unit(self) -> None:
        self.assertEqual(implementation_design_budget(1), 5000)
        self.assertEqual(implementation_design_budget(3), 8000)
        self.assertEqual(implementation_design_budget(4), 9500)
        self.assertEqual(implementation_design_budget(10), 16000)
        self.assertEqual(implementation_unit_budget(), 4500)

    def test_token_budget_tracks_unit_hint(self) -> None:
        from app.domain.architecture.service import llm_token_budget_for_design

        self.assertEqual(llm_token_budget_for_design("implementation-api", unit_count=1), 7500)
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


if __name__ == "__main__":
    unittest.main()

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
        self.assertEqual(plan.work_items[0].output_slot, "blueprint")
        self.assertEqual(
            {item.output_slot for item in plan.work_items[1:4]},
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
                    output_slot=item,
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


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest

from app.artifact.repository import ArtifactRef
from app.artifact.store import ArtifactStore
from app.execution_context import ExecutionMode
from app.orchestration.trace import TraceContext
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft
from app.planner.validator import PlanValidator
from app.agent.registry import AgentDefinition, AgentRegistry
from app.workflow.compiler import TemplateCompiler
from app.workflow.template import TaskBlueprint, WorkflowTemplate, WorkflowTemplateRegistry
from app.workflow.templates import (
    architecture_compact_template,
    architecture_parallel_template,
    project_delivery_layered_template,
)


class WorkflowCompilerTest(unittest.TestCase):
    def test_layered_delivery_template_contains_architecture_barrier(self) -> None:
        template = project_delivery_layered_template()
        self.assertEqual(template.nodes[0].id, "requirement")
        self.assertEqual(template.nodes[1].id, "architecture-blueprint")
        contract = next(node for node in template.nodes if node.id == "architecture-layered-contract")
        tasks = next(node for node in template.nodes if node.id == "tasks-plan")
        self.assertIn("architecture-layered-quality-gate", tasks.depends_on)
        self.assertIn("architecture-layered-contract", tasks.depends_on)
        self.assertEqual(contract.execution_mode, ExecutionMode.INTEGRATION)

    def test_compiles_controlled_template_into_scopes_integration_and_gate(self) -> None:
        template = architecture_parallel_template()
        trace = TraceContext(requirement_id="req-architecture", trace_id="tr-architecture")

        plan = TemplateCompiler().compile(
            template,
            goal="设计系统架构",
            plan_id="architecture-parallel",
            trace=trace,
            agent_output_keys={"architecture_agent": "architecture"},
        )

        self.assertEqual(len(plan.work_items), 6)
        self.assertEqual(
            [item.execution_mode for item in plan.work_items],
            [
                ExecutionMode.PARTITIONED,
                ExecutionMode.PARTITIONED,
                ExecutionMode.PARTITIONED,
                ExecutionMode.PARTITIONED,
                ExecutionMode.INTEGRATION,
                ExecutionMode.QUALITY_GATE,
            ],
        )
        baseline, api, data, frontend, integration, gate = plan.work_items
        self.assertEqual(baseline.output_slot, "baseline")
        self.assertEqual(api.input_refs[0].ref_id, "staged:tr-architecture:wi-01-architecture-baseline:baseline")
        self.assertEqual(
            {ref.ref_id for ref in integration.input_refs},
            {
                "staged:tr-architecture:wi-02-architecture-api:api",
                "staged:tr-architecture:wi-03-architecture-data:data",
                "staged:tr-architecture:wi-04-architecture-frontend:frontend",
            },
        )
        self.assertEqual(gate.candidate_from_work_item_id, integration.id)
        self.assertEqual(gate.dependency_ids, (integration.id,))

    def test_compiler_rejects_unknown_agent_before_execution(self) -> None:
        template = architecture_parallel_template()
        with self.assertRaisesRegex(ValueError, "未注册 Agent"):
            TemplateCompiler().compile(
                template,
                goal="设计架构",
                plan_id="unknown-agent",
                trace=TraceContext(requirement_id="req", trace_id="tr-compiler"),
                agent_output_keys={},
            )

    def test_compact_template_compiles_short_contracts_without_parallel_scopes(self) -> None:
        plan = TemplateCompiler().compile(
            architecture_compact_template(),
            goal="设计小型应用",
            plan_id="compact",
            trace=TraceContext(requirement_id="req", trace_id="tr-compact"),
            agent_output_keys={"architecture_agent": "architecture"},
        )

        self.assertEqual(len(plan.work_items), 3)
        self.assertEqual(plan.work_items[0].output_slot, "design")
        self.assertTrue(plan.work_items[0].acceptance_criteria)
        self.assertEqual(plan.work_items[-1].execution_mode, ExecutionMode.QUALITY_GATE)

    def test_planner_selecting_controlled_template_uses_template_not_llm_steps(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            artifacts = ArtifactStore(project_path)
            artifacts.save("requirement", "# requirement")
            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    id="architecture_agent", domain="architecture",
                    description="架构设计", output_key="architecture",
                ),
                factory=lambda: None,
            )
            templates = WorkflowTemplateRegistry()
            templates.register(architecture_parallel_template())
            context = PlanningContext.build(
                goal="并行设计架构", agents=agents, templates=templates, artifacts=artifacts
            )
            draft = PlanDraft.model_validate(
                {
                    "rationale": "使用受控架构模板",
                    "template_hint_id": "architecture_parallel",
                    "steps": [],
                }
            )

            plan = PlanValidator().validate(
                draft, context=context, plan_id="controlled-plan",
                trace=TraceContext(requirement_id="req", trace_id="tr-controlled"),
            )

        self.assertEqual(plan.template_id, "architecture_parallel")
        self.assertEqual(plan.work_items[0].input_refs, (ArtifactRef.published("requirement"),))
        self.assertEqual(plan.work_items[-1].execution_mode, ExecutionMode.QUALITY_GATE)

    def test_template_rejects_invalid_quality_gate_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "QUALITY_GATE"):
            WorkflowTemplate(
                id="invalid",
                name="invalid",
                description="invalid",
                nodes=(
                    TaskBlueprint(
                        id="gate", agent_id="architecture_agent", objective="publish",
                        output_key="architecture", execution_mode=ExecutionMode.QUALITY_GATE,
                        publish_target="architecture",
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()

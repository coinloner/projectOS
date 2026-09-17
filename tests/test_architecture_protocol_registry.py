import unittest
from dataclasses import replace
from app.artifact.repository import ArtifactRef
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import WorkItem, WorkItemDependency, DependencySource

from app.execution_context import ExecutionMode
from app.orchestration.delivery_registry import DeliveryContractRegistry
from app.workflow.templates import architecture_only_template


class ArchitectureProtocolRegistryTest(unittest.TestCase):
    def contract(self, mode, slot=None, stage=None):
        return DeliveryContractRegistry.contract_for(
            agent_id="architecture_agent", execution_mode=mode, slot=slot,
            stage_id=stage, publish_target="architecture",
        )

    def test_markdown_packages_validate_as_markdown_not_structured_json(self):
        for slot in ("design", "baseline", "api", "data", "frontend"):
            with self.subTest(slot=slot):
                contract = self.contract(ExecutionMode.PARTITIONED, slot)
                self.assertEqual(contract.expected_tool, "write_staged_architecture")
                self.assertEqual(contract.output_artifact_kind, "architecture_markdown")
                self.assertEqual(DeliveryContractRegistry.validate_content(contract, "# Design"), "# Design")
                with self.assertRaises(ValueError):
                    DeliveryContractRegistry.validate_content(contract, " \n")

    def test_integration_protocols_have_distinct_terminal_tools(self):
        markdown = self.contract(ExecutionMode.INTEGRATION, stage="architecture_markdown_integration")
        structured = self.contract(ExecutionMode.INTEGRATION, stage="architecture_integration")
        self.assertEqual(markdown.expected_tool, "create_architecture_candidate")
        self.assertEqual(structured.expected_tool, "integrate_architecture_designs")

    def test_explicit_unknown_integration_stage_cannot_use_legacy_fallback(self):
        for stage in ("unknown-integration", ""):
            with self.subTest(stage=stage):
                self.assertIsNone(DeliveryContractRegistry.contract_for(
                    agent_id="architecture_agent", execution_mode=ExecutionMode.INTEGRATION,
                    slot=None, stage_id=stage, publish_target="architecture",
                    work_item_id="wi-architecture-layered-integration",
                ))
        self.assertEqual(self.contract(ExecutionMode.INTEGRATION).kind,
                         "architecture.integration")

    def test_unknown_partition_does_not_inherit_markdown_contract(self):
        self.assertIsNone(self.contract(ExecutionMode.PARTITIONED, "unknown-slot"))

    def test_gate_consumes_integration_candidate_not_partitioned_blueprint(self):
        nodes = {node.id: node for node in architecture_only_template().nodes}
        integration = nodes["architecture-integration"]
        gate = nodes["architecture-quality-gate"]
        self.assertEqual(integration.execution_mode, ExecutionMode.INTEGRATION)
        self.assertEqual(integration.input_from, ("architecture-blueprint",))
        self.assertEqual(gate.candidate_from, integration.id)
        self.assertEqual(gate.depends_on, (integration.id,))


class ArchitectureProtocolEdgeTest(unittest.TestCase):
    def setUp(self):
        self.trace = TraceContext(requirement_id="req-edge", trace_id="tr-edge")
        self.producer = WorkItem(id="producer", agent_id="architecture_agent", objective="design", output_key="architecture",
                                 artifact_key="architecture", execution_mode=ExecutionMode.PARTITIONED, slot="api")
        self.consumer = WorkItem(id="consumer", agent_id="architecture_agent", objective="integrate", output_key="architecture",
                                 artifact_key="architecture", execution_mode=ExecutionMode.INTEGRATION, publish_target="architecture",
                                 stage_id="architecture_markdown_integration",
                                 dependencies=(WorkItemDependency("producer", DependencySource.SYSTEM),),
                                 input_refs=(ArtifactRef.staged(artifact_key="architecture", trace_id=self.trace.trace_id,
                                                              work_item_id="producer", slot="api"),))

    def plan(self, *items):
        return ExecutionPlan(id="edge-plan", goal="validate protocol", trace=self.trace, work_items=items)

    def test_matching_protocol_is_accepted(self):
        self.plan(self.producer, self.consumer)

    def test_mixed_protocol_is_rejected(self):
        consumer = replace(self.consumer, stage_id="architecture_integration", required_tools=(), contract_digest=None)
        with self.assertRaisesRegex(ValueError, "交付协议不匹配"):
            self.plan(self.producer, consumer)

    def test_reference_without_dependency_barrier_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未等待"):
            self.plan(self.producer, replace(self.consumer, dependencies=(), contract_digest=None))

    def test_current_run_missing_producer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "生产者不在"):
            self.plan(replace(self.consumer, dependencies=(), contract_digest=None))

    def test_gate_cannot_consume_partitioned_output(self):
        gate = WorkItem(id="gate", agent_id="architecture_agent", objective="publish", output_key="architecture",
                        execution_mode=ExecutionMode.QUALITY_GATE, publish_target="architecture",
                        candidate_from_work_item_id="producer", dependencies=(WorkItemDependency("producer", DependencySource.SYSTEM),))
        with self.assertRaisesRegex(ValueError, "INTEGRATION"):
            self.plan(self.producer, gate)

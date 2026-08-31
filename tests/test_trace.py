import json
import tempfile
import unittest
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.tool_manager.gateway import ToolGateway
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.execution_context import ExecutionContext
from app.orchestration.trace import TraceStore
from app.orchestration.trace import _infer_repair_paths
from app.orchestration.delivery import DeliveryStore
from app.orchestration.state import RunState
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.llm.config import resolve_llm_selection


class CompletedRequirementAgent:
    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        return AgentResult.completed("# Requirement\n\nFirst revision")


class FailingRequirementAgent:
    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        raise RuntimeError("simulated interruption")


class TraceStoreTest(unittest.TestCase):

    def test_requirement_snapshot_initializes_delivery_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("保存验收标准")
            traces.snapshot_requirement(
                context, "## 验收标准\n\n1. **AC-1 可创建订单**"
            )
            matrix = DeliveryStore(project_path).load_matrix()
            self.assertIn("AC-1", matrix.requirements)

    def test_infer_repair_paths_excludes_tests_and_maps_import_modules(self) -> None:
        stdout = "ERROR tests/infrastructure/test_postgresql.py\nfrom app.application.ports import IdempotencyEntry"
        stderr = "backend/app/infrastructure/repositories.py:18: ImportError"
        self.assertEqual(
            _infer_repair_paths(stdout, stderr),
            (
                "backend/app/application/ports.py",
                "backend/app/infrastructure/repositories.py",
            ),
        )

    def test_infer_repair_paths_uses_layer_fallback_when_trace_has_no_owner(self) -> None:
        stdout = (
            "FAILED tests/api/test_health_http_regression.py\n"
            "FAILED tests/application/test_orders.py\n"
        )
        self.assertEqual(
            _infer_repair_paths(stdout, ""),
            (
                "backend/app/api/**",
                "backend/app/application/**",
                "backend/app/main.py",
            ),
        )

    def test_mark_running_clears_historical_terminal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("恢复运行")
            traces.finish_trace(context, "failed", error="旧错误")
            traces.mark_running(context.trace_id)
            payload = traces.load_trace(context.trace_id)
            self.assertEqual(payload["status"], "running")
            self.assertNotIn("finished_at", payload)
            self.assertNotIn("error", payload)

    def test_trace_persists_default_and_agent_llm_routes_without_keys(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("验证模型路由")
            default = resolve_llm_selection("siliconflow", model="zai-org/GLM-5.2")
            review = resolve_llm_selection("openai", model="gpt-4.1")

            traces.set_llm_selection(context.trace_id, default)
            traces.set_llm_overrides(context.trace_id, {"review_agent": review})

            loaded = traces.load_llm_selection(context.trace_id)
            overrides = traces.load_llm_overrides(context.trace_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.model, "zai-org/GLM-5.2")
            self.assertEqual(overrides["review_agent"].provider, "openai")
            payload = json.loads(
                (Path(project_path) / ".projectos" / "runs" / context.trace_id / "trace.json").read_text()
            )
            self.assertNotIn('"api_key":', json.dumps(payload))

    def test_trace_records_plan_events_and_requirement_revision(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("生成需求")
            plan = ExecutionPlan(
                id="plan-001",
                goal="生成需求",
                trace=context,
                work_items=(
                    WorkItem(
                        id="wi-01-requirement",
                        agent_id="requirement_agent",
                        objective="保存需求文档",
                        output_key="requirement",
                    ),
                ),
            )
            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    id="requirement_agent",
                    domain="requirement",
                    description="整理需求",
                    output_key="requirement",
                ),
                factory=CompletedRequirementAgent,
            )

            result = GraphRunner(agents, ToolGateway(), traces=traces).run(plan)

            root = Path(project_path) / ".projectos"
            plan_payload = json.loads(
                (root / "runs" / context.trace_id / "plan.json").read_text()
            )
            events = [
                json.loads(line)
                for line in (root / "runs" / context.trace_id / "events.jsonl")
                .read_text()
                .splitlines()
            ]
            requirement = json.loads((root / "requirement.json").read_text())
            trace_metadata = json.loads(
                (root / "runs" / context.trace_id / "trace.json").read_text()
            )
            snapshot_exists = (root / "requirements" / "revision-1.md").exists()

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)
            self.assertEqual(plan_payload["work_items"][0]["id"], "wi-01-requirement")
            self.assertEqual(requirement["requirement_id"], context.requirement_id)
            self.assertEqual(requirement["current_revision"], 1)
            self.assertEqual(trace_metadata["status"], "completed")
            self.assertTrue(snapshot_exists)
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "work_item_planned",
                    "work_item_started",
                    "work_item_completed",
                    "requirement_snapshot",
                ],
            )

    def test_trace_reuses_requirement_identity_and_versions_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            first = traces.start_trace("第一次需求")
            first_revision = traces.snapshot_requirement(first, "# Requirement\n\nV1")
            second = traces.start_trace("需求更新", parent_trace_id=first.trace_id)
            second_revision = traces.snapshot_requirement(second, "# Requirement\n\nV2")
            metadata = json.loads(
                (Path(project_path) / ".projectos" / "requirement.json").read_text()
            )

            self.assertEqual(first.requirement_id, second.requirement_id)
            self.assertNotEqual(first.trace_id, second.trace_id)
            self.assertEqual(second.parent_trace_id, first.trace_id)
            self.assertEqual((first_revision, second_revision), (1, 2))
            self.assertEqual(metadata["current_revision"], 2)

    def test_plan_and_checkpoint_can_rebuild_and_resume_failed_node(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("恢复执行")
            plan = ExecutionPlan(
                id="plan-resume",
                goal="恢复执行",
                trace=context,
                work_items=(
                    WorkItem(
                        id="wi-resume",
                        agent_id="requirement_agent",
                        objective="生成需求",
                        output_key="requirement",
                    ),
                ),
            )
            failing = AgentRegistry()
            failing.register(
                AgentDefinition(
                    id="requirement_agent",
                    domain="requirement",
                    description="失败 Agent",
                    output_key="requirement",
                ),
                factory=FailingRequirementAgent,
            )
            first = GraphRunner(failing, ToolGateway(), traces=traces).run(plan)
            self.assertEqual(first.status, GraphRunStatus.FAILED)

            restored_plan = traces.load_plan(context.trace_id)
            checkpoint = traces.load_checkpoint(context.trace_id)
            self.assertIn("artifact_refs", checkpoint)
            self.assertNotIn("artifacts", checkpoint)
            restored_state = RunState.from_checkpoint(restored_plan, checkpoint)
            self.assertEqual(restored_state.node_results, {})

            # A completed checkpoint with a changed contract must not be
            # accepted during restore.
            checkpoint["artifact_refs"] = {
                "requirement": {
                    "artifact_key": "requirement",
                    "work_item_id": "wi-resume",
                    "contract_digest": "tampered",
                }
            }
            with self.assertRaisesRegex(ValueError, "合同指纹"):
                RunState.from_checkpoint(restored_plan, checkpoint)

            succeeding = AgentRegistry()
            succeeding.register(
                AgentDefinition(
                    id="requirement_agent",
                    domain="requirement",
                    description="恢复 Agent",
                    output_key="requirement",
                ),
                factory=CompletedRequirementAgent,
            )
            resumed = GraphRunner(
                succeeding, ToolGateway(), traces=traces
            ).run(restored_plan, state=restored_state)
            self.assertEqual(resumed.status, GraphRunStatus.COMPLETED)
            event_types = [
                event["type"] for event in traces.list_events(context.trace_id)
            ]
            self.assertIn("run_resumed", event_types)

    def test_plan_baseline_is_versioned_without_changing_trace_plan_schema(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("局部修改")
            plan = ExecutionPlan(
                id="plan-baseline",
                goal="局部修改",
                trace=context,
                work_items=(
                    WorkItem(
                        id="wi-baseline",
                        agent_id="requirement_agent",
                        objective="生成需求",
                        output_key="requirement",
                    ),
                ),
            )
            first = traces.record_plan_baseline(plan)
            second = traces.record_plan_baseline(plan)

            self.assertEqual(first["revision"], 1)
            self.assertEqual(second["revision"], 2)
            self.assertEqual(traces.load_plan_baseline(context.trace_id)["revision"], 2)

    def test_delivery_plan_survives_active_repair_plan_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            context = traces.start_trace("交付基线")
            original = ExecutionPlan(
                id="plan-delivery",
                goal="交付基线",
                template_id="project_delivery",
                trace=context,
                work_items=(
                    WorkItem(
                        id="wi-original-tests",
                        agent_id="test_agent",
                        objective="运行测试",
                        output_key="tests",
                    ),
                    WorkItem(
                        id="wi-original-review",
                        agent_id="review_agent",
                        objective="审查交付",
                        output_key="review",
                        dependencies=(
                            WorkItemDependency(
                                "wi-original-tests", DependencySource.SYSTEM
                            ),
                        ),
                    ),
                ),
            )
            repair = ExecutionPlan(
                id="plan-delivery-repair-1",
                goal="交付基线",
                trace=context,
                work_items=(
                    WorkItem(
                        id="wi-repair-tests",
                        agent_id="test_agent",
                        objective="修复测试",
                        output_key="tests",
                    ),
                ),
            )
            traces.record_plan(original)
            traces.record_delivery_plan(original)
            traces.record_plan(repair)

            self.assertEqual(traces.load_plan(context.trace_id).id, repair.id)
            self.assertEqual(traces.load_delivery_plan(context.trace_id).id, original.id)

    def test_finish_trace_clears_intermediate_error_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("恢复")
            traces.finish_trace(trace, "waiting_for_capability_approval", error="旧阻塞")
            traces.finish_trace(trace, "completed")
            loaded = traces.load_trace(trace.trace_id)
            self.assertEqual(loaded["status"], "completed")
            self.assertNotIn("error", loaded)


if __name__ == "__main__":
    unittest.main()

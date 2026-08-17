import json
import tempfile
import unittest
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.tool_manager.gateway import ToolGateway
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import WorkItem


class CompletedRequirementAgent:
    def run(self, task: str) -> AgentResult:
        return AgentResult.completed("# Requirement\n\nFirst revision")


class TraceStoreTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

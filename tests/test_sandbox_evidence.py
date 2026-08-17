import json
import tempfile
import unittest
from pathlib import Path

from app.domain.review.tools import SandboxEvidenceReaderToolSet
from app.domain.test.service import TestService
from app.domain.test.tools import SandboxEvidenceToolSet
from app.execution_context import ExecutionContext
from app.orchestration.trace import TraceStore
from app.sandbox.result import SandboxResult, SandboxStatus
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef


class FakeSandboxController:
    def __init__(self, result: SandboxResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def run_check(self, project_path: str, check_id: str) -> SandboxResult:
        self.calls.append((project_path, check_id))
        return self._result


class SandboxEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        self.traces = TraceStore(self.project_path)
        self.trace = self.traces.start_trace("验证受控 Docker 结果")
        self.test_context = ExecutionContext(
            trace_id=self.trace.trace_id,
            work_item_id="wi-test",
            agent_id="test_agent",
        )
        self.review_context = ExecutionContext(
            trace_id=self.trace.trace_id,
            work_item_id="wi-review",
            agent_id="review_agent",
        )
        self.sandbox = FakeSandboxController(
            SandboxResult(
                status=SandboxStatus.FAILED,
                check_id="unit",
                runtime_profile="python-stdlib",
                exit_code=1,
                duration_ms=38,
                stdout="Ran 1 test",
                stderr="AssertionError: expected 2",
            )
        )

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_sandbox_result_is_persisted_and_linked_to_the_active_work_item(self) -> None:
        service = TestService(self.project_path, sandbox=self.sandbox)
        tools = SandboxEvidenceToolSet(service, self.traces)
        gateway = ToolGateway()
        gateway.register_toolset(
            domain="test",
            name="sandbox_runner",
            toolset=ExecutionToolSetSource(
                [
                    (
                        ToolDef(
                            name="run_sandbox_check",
                            description="run fixed unit check",
                            parameters={"type": "object", "properties": {}},
                        ),
                        tools.run_sandbox_check,
                    )
                ]
            ),
        )

        tool = gateway.tools_for("test", context=self.test_context)[0]
        response = tool.run()
        evidence_id = response.splitlines()[0].split("=", 1)[1]
        evidence_path = (
            Path(self.project_path)
            / ".projectos"
            / "runs"
            / self.trace.trace_id
            / "evidence"
            / f"{evidence_id}.json"
        )
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        events = [
            json.loads(line)
            for line in (
                Path(self.project_path)
                / ".projectos"
                / "runs"
                / self.trace.trace_id
                / "events.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]

        self.assertEqual(tool.args_schema.model_json_schema()["properties"], {})
        self.assertEqual(self.sandbox.calls, [(self.project_path, "unit")])
        self.assertEqual(payload["trace_id"], self.trace.trace_id)
        self.assertEqual(payload["work_item_id"], "wi-test")
        self.assertEqual(payload["agent_id"], "test_agent")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["stdout"], "Ran 1 test")
        self.assertEqual(payload["stderr"], "AssertionError: expected 2")
        self.assertEqual(events[-1]["type"], "sandbox_evidence_recorded")
        self.assertEqual(events[-1]["details"]["evidence_id"], evidence_id)
        self.assertEqual(events[-1]["details"]["status"], "failed")

    def test_execution_bound_tool_rejects_a_call_without_graph_context(self) -> None:
        service = TestService(self.project_path, sandbox=self.sandbox)
        source = ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="run_sandbox_check",
                        description="run fixed unit check",
                        parameters={"type": "object", "properties": {}},
                    ),
                    SandboxEvidenceToolSet(service, self.traces).run_sandbox_check,
                )
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "GraphRunner"):
            source.execute("run_sandbox_check", {})
        self.assertEqual(self.sandbox.calls, [])

    def test_review_can_read_current_trace_evidence_but_test_cannot_access_review_tools(self) -> None:
        evidence = self.traces.record_sandbox_evidence(
            self.test_context, self.sandbox.run_check(self.project_path, "unit")
        )
        reader = SandboxEvidenceReaderToolSet(self.traces)
        gateway = ToolGateway()
        gateway.register_toolset(
            domain="review",
            name="sandbox_evidence",
            toolset=ExecutionToolSetSource(
                [
                    (
                        ToolDef(
                            name="list_sandbox_evidence",
                            description="list evidence",
                            parameters={"type": "object", "properties": {}},
                        ),
                        reader.list_sandbox_evidence,
                    ),
                    (
                        ToolDef(
                            name="load_sandbox_evidence",
                            description="load evidence",
                            parameters={
                                "type": "object",
                                "properties": {"evidence_id": {"type": "string"}},
                                "required": ["evidence_id"],
                            },
                        ),
                        reader.load_sandbox_evidence,
                    ),
                ]
            ),
        )

        review_tools = {
            tool.name: tool
            for tool in gateway.tools_for("review", context=self.review_context)
        }

        self.assertIn(evidence.id, review_tools["list_sandbox_evidence"].run())
        loaded = review_tools["load_sandbox_evidence"].run(evidence_id=evidence.id)
        self.assertIn("status=failed", loaded)
        self.assertIn("AssertionError: expected 2", loaded)
        self.assertEqual(gateway.tools_for("test", context=self.test_context), [])

        other_trace = self.traces.start_trace("另一次执行")
        other_context = ExecutionContext(
            trace_id=other_trace.trace_id,
            work_item_id="wi-review-other",
            agent_id="review_agent",
        )
        with self.assertRaisesRegex(FileNotFoundError, "当前 Trace"):
            self.traces.load_sandbox_evidence(other_context, evidence.id)


if __name__ == "__main__":
    unittest.main()

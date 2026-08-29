import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.orchestration.progress import (
    ProgressTracker,
    WorkerProgressStore,
    heartbeat_for,
    idle_for,
    phase_idle_timeout,
)
from app.project.project import Project


class ProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        Project.create_at(self.directory.name, name="progress-demo")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_tracker_persists_stream_and_tool_counters_without_content(self) -> None:
        tracker = ProgressTracker(
            self.directory.name, "tr-progress", "implementation", "code_agent"
        )
        tracker.llm_started(type("Event", (), {"stream": True, "call_id": "call-1"})())
        tracker.llm_chunk(type("Event", (), {"chunk": "secret token"})())
        tracker.tool_started("write_workspace_file")
        tracker.tool_completed("write_workspace_file")

        payload = WorkerProgressStore(self.directory.name).read("tr-progress")
        assert payload is not None
        self.assertEqual(payload["phase"], "running_tool")
        self.assertEqual(payload["counters"]["llm_chunks"], 1)
        self.assertEqual(payload["counters"]["tool_calls"], 1)
        self.assertNotIn("secret token", str(payload))
        self.assertIn("implementation", payload["work_items"])
        self.assertIsNotNone(payload.get("heartbeat_at"))
        self.assertIsNotNone(heartbeat_for(payload))

    def test_idle_timeout_is_phase_specific_and_stream_progress_resets_idle(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        self.assertGreater(idle_for({"last_progress_at": old}) or 0, 9)
        with patch.dict("os.environ", {"PROJECTOS_IDLE_LLM_STREAMING_SECONDS": "7"}):
            self.assertEqual(phase_idle_timeout("llm_streaming"), 7)

    def test_provider_stall_does_not_cancel_following_work_items(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.request_provider_stall("tr-control", reason="slow provider")
        self.assertFalse(store.cancel_requested("tr-control"))
        store.request_cancel("tr-control", reason="hard stop")
        self.assertTrue(store.cancel_requested("tr-control"))

    def test_llm_terminal_state_is_explicit_and_not_chunk_derived(self) -> None:
        tracker = ProgressTracker(
            self.directory.name, "tr-terminal", "implementation", "code_agent"
        )
        tracker.llm_started(type("Event", (), {"stream": True, "call_id": "c1"})())
        payload = WorkerProgressStore(self.directory.name).read("tr-terminal")
        self.assertEqual(payload["llm"]["state"], "started")
        tracker.llm_completed_from_agent_return()
        tracker.llm_completed_from_agent_return()
        payload = WorkerProgressStore(self.directory.name).read("tr-terminal")
        self.assertEqual(payload["llm"]["state"], "completed")
        self.assertEqual(payload["event"], "llm_completed")

    def test_file_delivery_events_are_persisted_without_content(self) -> None:
        tracker = ProgressTracker(
            self.directory.name, "tr-write", "wi-code", "code_agent"
        )
        tracker.update("writing_file", "file_write_started", path="backend/app/main.py")
        tracker.update("writing_file", "file_write_succeeded", path="backend/app/main.py", bytes=12)
        tracker.update("changeset", "changeset_created", commit="abc")
        payload = WorkerProgressStore(self.directory.name).read("tr-write")
        assert payload is not None
        self.assertEqual(payload["event"], "changeset_created")
        self.assertIn("file_write_succeeded", str(payload["progress_events"]))
        self.assertNotIn("secret", str(payload))


if __name__ == "__main__":
    unittest.main()

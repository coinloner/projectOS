import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from app.execution_context import ExecutionContext
from app.orchestration.progress import (
    MONITOR_SCHEMA_VERSION,
    ExecutionActivity,
    ExecutionLifecycle,
    ExecutionOutcome,
    ProgressTracker,
    WorkerHeartbeat,
    WorkerProgressStore,
    canonical_progress,
    heartbeat_for,
    meaningful_idle_for,
    track_planning_stream,
    transport_idle_for,
)
from app.orchestration.progress_tools import report_progress
from app.project.project import Project


class ProgressMonitorV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        Project.create_at(self.directory.name, name="monitor-v3")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_work_item_tracker_writes_only_v3_nested_state(self) -> None:
        tracker = ProgressTracker(self.directory.name, "tr-progress", "wi-code", "code_agent")
        tracker.llm_started(type("Event", (), {"stream": True, "call_id": "call-1"})())
        tracker.llm_chunk(type("Event", (), {"chunk": "secret token"})())
        tracker.tool_started("write_workspace_file")
        tracker.tool_completed("write_workspace_file", result="ok")

        payload = WorkerProgressStore(self.directory.name).read("tr-progress")
        assert payload is not None
        self.assertEqual(payload["schema_version"], MONITOR_SCHEMA_VERSION)
        self.assertEqual(set(payload), {"schema_version", "trace_id", "run", "batches", "work_items"})
        self.assertNotIn("phase", payload)
        self.assertNotIn("progress_events", payload)
        self.assertNotIn("working_state", payload)
        item = payload["work_items"]["wi-code"]
        self.assertEqual(item["activity"], "tool")
        self.assertEqual(item["counters"]["llm_chunks"], 1)
        self.assertEqual(item["counters"]["tool_calls"], 1)
        self.assertNotIn("secret token", str(payload))
        self.assertEqual(payload["run"]["counters"]["llm_chunks"], 1)

    def test_transport_activity_does_not_refresh_meaningful_clock(self) -> None:
        tracker = ProgressTracker(self.directory.name, "tr-clocks", "wi-code", "code_agent")
        tracker.llm_started(type("Event", (), {"stream": True, "call_id": "call-1"})())
        before = WorkerProgressStore(self.directory.name).read("tr-clocks")
        assert before is not None
        meaningful = before["work_items"]["wi-code"]["last_meaningful_at"]
        time.sleep(0.002)
        tracker.llm_chunk(type("Event", (), {"chunk": "private output"})())
        after = WorkerProgressStore(self.directory.name).read("tr-clocks")
        assert after is not None
        item = after["work_items"]["wi-code"]
        self.assertEqual(item["last_meaningful_at"], meaningful)
        self.assertIsNotNone(item["clocks"]["transport_at"])
        self.assertNotIn("private output", str(after))

    def test_semantic_report_is_deduplicated_without_completion_claim(self) -> None:
        tracker = ProgressTracker(self.directory.name, "tr-semantic", "wi-code", "code_agent")
        self.assertTrue(tracker.report_semantic(
            work_stage="implementation", summary="已核对接口", next_action="写入服务"
        ))
        self.assertFalse(tracker.report_semantic(
            work_stage="implementation", summary="已核对接口", next_action="写入服务"
        ))
        payload = WorkerProgressStore(self.directory.name).read("tr-semantic")
        assert payload is not None
        item = payload["work_items"]["wi-code"]
        self.assertEqual(item["event_type"], "agent_progress_reported")
        self.assertEqual(item["required_action"], "写入服务")
        self.assertEqual(item["lifecycle"], "running")

    def test_batch_has_its_own_fan_out_fan_in_state(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.start_run("tr-batch")
        store.start_batch(
            "tr-batch", "batch-w0-a-b", wave_index=0, work_item_ids=("wi-a", "wi-b")
        )
        payload = store.read("tr-batch")
        assert payload is not None
        batch = payload["batches"]["batch-w0-a-b"]
        self.assertEqual(batch["lifecycle"], "running")
        self.assertEqual(batch["barrier_state"], "waiting_for_members")
        self.assertEqual(payload["run"]["active_batch_id"], "batch-w0-a-b")

        store.finish_batch(
            "tr-batch", "batch-w0-a-b", completed=("wi-a",), failed=("wi-b",),
            outcome=ExecutionOutcome.FAILED, root_failure_work_item_id="wi-b",
            error_code="provider_stall",
        )
        payload = store.read("tr-batch")
        assert payload is not None
        batch = payload["batches"]["batch-w0-a-b"]
        self.assertEqual(batch["lifecycle"], "terminal")
        self.assertEqual(batch["outcome"], "failed")
        self.assertEqual(batch["root_failure_work_item_id"], "wi-b")
        self.assertEqual(batch["completed_work_item_ids"], ["wi-a"])
        self.assertEqual(payload["run"]["active_batch_id"], None)

    def test_forced_run_finalization_distinguishes_root_failure_and_interruption(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.start_run("tr-finalize")
        for item_id in ("wi-root", "wi-sibling"):
            store.record_work_item_activity(
                "tr-finalize", item_id, "code_agent",
                lifecycle=ExecutionLifecycle.RUNNING,
                activity=ExecutionActivity.LLM,
                event_type="llm_chunk_batch",
                signal=store_signal_transport(),
                llm={"state": "streaming", "call_id": item_id, "terminal_at": None},
            )
        store.start_batch(
            "tr-finalize", "batch-w0", wave_index=0,
            work_item_ids=("wi-root", "wi-sibling"),
        )
        closed = store.finalize_run(
            "tr-finalize", outcome=ExecutionOutcome.FAILED,
            event="provider_stall_timeout", summary="Provider 停滞",
            details={"failure_kind": "provider_stall"}, root_work_item_id="wi-root",
        )
        self.assertEqual(set(closed), {"wi-root", "wi-sibling"})
        payload = store.read("tr-finalize")
        assert payload is not None
        self.assertEqual(payload["run"]["lifecycle"], "terminal")
        self.assertEqual(payload["run"]["outcome"], "failed")
        self.assertEqual(payload["work_items"]["wi-root"]["outcome"], "failed")
        self.assertEqual(payload["work_items"]["wi-sibling"]["outcome"], "interrupted")
        self.assertEqual(payload["batches"]["batch-w0"]["root_failure_work_item_id"], "wi-root")
        self.assertEqual(payload["batches"]["batch-w0"]["interrupted_work_item_ids"], ["wi-sibling"])

    def test_planning_is_run_level_without_synthetic_work_item(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.record_planning_state(
            "tr-plan", lifecycle=ExecutionLifecycle.RUNNING,
            event="planning_started", summary="规划开始", attempt=1,
        )
        with track_planning_stream(store, "tr-plan", attempt=1) as tracker:
            tracker.llm_started(type("Event", (), {})())
            tracker.llm_chunk(type("Event", (), {"chunk": "你好"})())
            tracker.llm_completed(type("Event", (), {})())
        payload = store.read("tr-plan")
        assert payload is not None
        self.assertEqual(payload["run"]["event_type"], "planning_llm_completed")
        self.assertEqual(payload["run"]["counters"]["llm_calls"], 1)
        self.assertEqual(payload["run"]["counters"]["llm_chunks"], 1)
        self.assertEqual(payload["work_items"], {})

    def test_heartbeat_updates_process_observation_only(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.start_run("tr-heartbeat")
        before = store.read("tr-heartbeat")
        assert before is not None
        lifecycle = before["run"]["lifecycle"]
        heartbeat = WorkerHeartbeat(self.directory.name, "tr-heartbeat", interval=0.01)
        heartbeat.start()
        time.sleep(0.04)
        heartbeat.stop()
        after = store.read("tr-heartbeat")
        assert after is not None
        self.assertEqual(after["run"]["lifecycle"], lifecycle)
        self.assertIsNotNone(after["run"]["heartbeat_at"])
        self.assertIsNotNone(heartbeat_for(after))

    def test_api_projection_is_derived_and_has_no_legacy_phase(self) -> None:
        store = WorkerProgressStore(self.directory.name)
        store.start_run("tr-view")
        store.record_work_item_activity(
            "tr-view", "wi-a", "code_agent",
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event_type="llm_started",
            signal=store_signal_control(),
        )
        view = canonical_progress(store.read("tr-view"))
        assert view is not None
        self.assertEqual(view["summary"]["active_work_item_ids"], ["wi-a"])
        self.assertNotIn("phase", view)
        self.assertIn("meaningful_idle_seconds", view["run"])
        self.assertNotIn("overall_state", view)

    def test_agent_progress_tool_uses_new_required_action_field(self) -> None:
        tracker = ProgressTracker(self.directory.name, "tr-tool", "wi-code", "code_agent")
        context = ExecutionContext(
            trace_id="tr-tool", work_item_id="wi-code", agent_id="code_agent", progress=tracker,
        )
        response = report_progress(context, "analysis", "已读取合同", "调用保存工具", [])
        self.assertIn('"accepted": true', response)
        payload = WorkerProgressStore(self.directory.name).read("tr-tool")
        assert payload is not None
        self.assertEqual(payload["work_items"]["wi-code"]["required_action"], "调用保存工具")

    def test_idle_helpers_read_v3_clocks(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        view = {
            "run": {"last_meaningful_at": old, "heartbeat_at": old},
            "clocks": {"transport_at": old},
        }
        self.assertGreater(meaningful_idle_for(view) or 0, 9)
        self.assertGreater(transport_idle_for(view) or 0, 9)


def store_signal_transport():
    from app.orchestration.progress import ProgressSignalKind
    return ProgressSignalKind.TRANSPORT


def store_signal_control():
    from app.orchestration.progress import ProgressSignalKind
    return ProgressSignalKind.CONTROL


if __name__ == "__main__":
    unittest.main()

import tempfile
import time
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

from app.application.runs import RunCoordinator
from app.bootstrap.runtime import build_container
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import WorkItem
from app.project.project import Project
from app.planner.service import PlannerFailure


class FakeProcess:
    def __init__(self, *, alive: bool, exitcode: int | None = 0) -> None:
        self.alive = alive
        self.exitcode = exitcode
        self.started = False
        self.terminated = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


class FakeContext:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process

    def Process(self, *, target, args, name):  # noqa: N802 - multiprocessing API
        return self.process


class RunCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        Project.create_at(self.directory.name, name="demo")
        self.trace = TraceStore(self.directory.name).start_trace("测试 Worker")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_worker_timeout_terminates_process_and_finishes_trace(self) -> None:
        process = FakeProcess(alive=True, exitcode=None)
        coordinator = RunCoordinator(worker_timeout_seconds=0.01)

        with patch(
            "app.application.runs.multiprocessing.get_context",
            return_value=FakeContext(process),
        ):
            result = coordinator._run_in_worker(self.directory.name, self.trace.trace_id)

        self.assertEqual(result, "failed")
        self.assertTrue(process.started)
        self.assertTrue(process.terminated)
        trace = TraceStore(self.directory.name).load_trace(self.trace.trace_id)
        self.assertEqual(trace["status"], "failed")
        events = TraceStore(self.directory.name).list_events(self.trace.trace_id)
        self.assertEqual(events[-1]["type"], "worker_timed_out")
        coordinator.shutdown()

    def test_worker_uses_persisted_trace_status_after_process_exit(self) -> None:
        process = FakeProcess(alive=False, exitcode=0)
        coordinator = RunCoordinator()

        TraceStore(self.directory.name).finish_trace(self.trace, "completed")
        with patch(
            "app.application.runs.multiprocessing.get_context",
            return_value=FakeContext(process),
        ):
            result = coordinator._run_in_worker(self.directory.name, self.trace.trace_id)

        self.assertEqual(result, "completed")
        self.assertTrue(process.started)
        coordinator.shutdown()

    def test_submit_rejects_duplicate_active_trace(self) -> None:
        coordinator = RunCoordinator()
        trace_id = self.trace.trace_id
        pending = Future()
        coordinator._futures[trace_id] = pending
        plan = SimpleNamespace(trace=SimpleNamespace(trace_id=trace_id))
        container = SimpleNamespace(project_path=self.directory.name, traces=SimpleNamespace())

        with self.assertRaises(PlannerFailure):
            coordinator.submit(container, plan)

        coordinator.shutdown()

    def test_shutdown_terminates_active_workers(self) -> None:
        process = FakeProcess(alive=True, exitcode=None)
        coordinator = RunCoordinator()
        coordinator._processes[self.trace.trace_id] = process

        coordinator.shutdown()

        self.assertTrue(process.terminated)

    def test_spawned_worker_rebuilds_runtime_and_persists_failure(self) -> None:
        container = build_container(self.directory.name)
        plan = ExecutionPlan(
            id="worker-smoke-plan",
            goal="验证隔离 Worker",
            trace=self.trace,
            work_items=(
                WorkItem(
                    id="unknown",
                    agent_id="missing_agent",
                    objective="触发结构化失败",
                    output_key="unknown_output",
                ),
            ),
        )
        container.traces.record_plan(plan)
        coordinator = RunCoordinator(worker_timeout_seconds=30)
        coordinator.submit(container, plan)

        deadline = time.monotonic() + 15
        status = "running"
        while time.monotonic() < deadline:
            status = coordinator.status(plan.trace.trace_id) or "missing"
            if status != "running":
                break
            time.sleep(0.1)

        self.assertEqual(status, "failed")
        trace = TraceStore(self.directory.name).load_trace(plan.trace.trace_id)
        self.assertEqual(trace["status"], "failed")
        event_types = [
            event["type"]
            for event in TraceStore(self.directory.name).list_events(plan.trace.trace_id)
        ]
        self.assertIn("worker_started", event_types)
        coordinator.shutdown()


if __name__ == "__main__":
    unittest.main()

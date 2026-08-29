"""运行进度、流式 LLM 事件和阶段级空闲检测。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from threading import Event, Thread
import time
from typing import Any

from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import (
    LLMCallCompletedEvent,
    LLMCallFailedEvent,
    LLMCallStartedEvent,
    LLMStreamChunkEvent,
)


_progress_file_lock = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seconds_since(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
    except (TypeError, ValueError):
        return None


class WorkerProgressStore:
    """以原子替换写入 Worker 状态，API 重启后仍可读。"""

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / ".projectos" / "runs"

    def path(self, trace_id: str) -> Path:
        return self._path / trace_id / "worker-status.json"

    def control_path(self, trace_id: str) -> Path:
        return self._path / trace_id / "worker-control.json"

    def read(self, trace_id: str) -> dict[str, Any] | None:
        path = self.path(trace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def write(self, trace_id: str, payload: dict[str, Any]) -> None:
        path = self.path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def request_cancel(self, trace_id: str, *, reason: str) -> None:
        self._write_control(trace_id, action="cancel", reason=reason)

    def request_provider_stall(self, trace_id: str, *, reason: str) -> None:
        """记录 Provider 无进度诊断，不取消后续 WorkItem。"""
        self._write_control(trace_id, action="provider_stalled", reason=reason)

    def _write_control(self, trace_id: str, *, action: str, reason: str) -> None:
        path = self.control_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"trace_id": trace_id, "action": action, "reason": reason, "requested_at": _now()}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def cancel_requested(self, trace_id: str) -> bool:
        path = self.control_path(trace_id)
        if not path.is_file():
            return False
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("action") == "cancel"
        except (OSError, ValueError):
            return False

    def clear_control(self, trace_id: str) -> None:
        try:
            self.control_path(trace_id).unlink()
        except FileNotFoundError:
            pass

    def lease_path(self, trace_id: str) -> Path:
        return self._path / trace_id / "worker-lease.json"

    def claim_worker(self, trace_id: str) -> bool:
        """Atomically claim a Trace for one Worker process.

        API requests can race across processes (for example a timed-out resume
        request followed by a retry).  A filesystem lease is the durable
        single-flight guard; stale leases are removed only when their owner is
        no longer alive.
        """
        path = self.lease_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"trace_id": trace_id, "pid": os.getpid(), "claimed_at": _now()}, ensure_ascii=False) + "\n"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                pid = int(current.get("pid", 0))
                if pid > 0:
                    os.kill(pid, 0)
                    return False
            except (OSError, ValueError, TypeError):
                pass
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return self.claim_worker(trace_id)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        return True

    def release_worker(self, trace_id: str) -> None:
        path = self.lease_path(trace_id)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if int(current.get("pid", 0)) != os.getpid():
                return
        except (OSError, ValueError, TypeError):
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def worker_active(self, trace_id: str) -> bool:
        path = self.lease_path(trace_id)
        if not path.is_file():
            return False
        try:
            pid = int(json.loads(path.read_text(encoding="utf-8")).get("pid", 0))
            os.kill(pid, 0)
            return pid > 0
        except (OSError, ValueError, TypeError):
            return False


class WorkerHeartbeat:
    """独立于 LLM/工具回调的 Worker 存活信号。"""

    def __init__(self, project_path: str, trace_id: str, *, interval: float = 5.0) -> None:
        self._store = WorkerProgressStore(project_path)
        self._trace_id = trace_id
        self._interval = max(1.0, interval)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(target=self._run, name=f"projectos-heartbeat-{self._trace_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            # Heartbeat and ProgressTracker run in the same Worker process.
            # Serialise the read/modify/atomic-replace sequence so two writers
            # cannot race on the shared ``worker-status.tmp`` path and make a
            # healthy LLM call look idle.
            with _progress_file_lock:
                payload = self._store.read(self._trace_id) or {}
                payload["heartbeat_at"] = _now()
                self._store.write(self._trace_id, payload)


@dataclass
class ProgressTracker:
    """单个 WorkItem 的进度发送器，不保存模型正文。"""

    project_path: str
    trace_id: str
    work_item_id: str
    agent_id: str
    _store: WorkerProgressStore = field(init=False)
    _lock: RLock = field(default_factory=RLock, init=False)
    _sequence: int = field(default=0, init=False)
    _chunks: int = field(default=0, init=False)
    _bytes: int = field(default=0, init=False)
    _llm_calls: int = field(default=0, init=False)
    _tool_calls: int = field(default=0, init=False)
    _llm_state: str = field(default="idle", init=False)
    _llm_call_id: str | None = field(default=None, init=False)
    _llm_terminal_at: str | None = field(default=None, init=False)
    _last_trace_event_at: float = field(default=0.0, init=False)
    _bound_agent_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._store = WorkerProgressStore(self.project_path)
        self.update("running", "work_item_started")

    def update(self, phase: str, event: str, **details: Any) -> None:
        with self._lock, _progress_file_lock:
            self._sequence += 1
            now = _now()
            current = self._store.read(self.trace_id) or {}
            started_at = current.get("started_at", now)
            payload = {
                **current,
                "trace_id": self.trace_id,
                "work_item_id": self.work_item_id,
                "agent_id": self.agent_id,
                "phase": phase,
                "event": event,
                "sequence": self._sequence,
                "started_at": started_at,
                "last_progress_at": now,
                "heartbeat_at": now,
                "elapsed_seconds": _seconds_since(started_at),
                "counters": {
                    "llm_calls": self._llm_calls,
                    "llm_chunks": self._chunks,
                    "llm_bytes": self._bytes,
                    "tool_calls": self._tool_calls,
                },
                "details": details,
                "llm": {
                    "state": self._llm_state,
                    "call_id": self._llm_call_id,
                    "terminal_at": self._llm_terminal_at,
                },
            }
            work_items = current.get("work_items")
            if not isinstance(work_items, dict):
                work_items = {}
            work_items[self.work_item_id] = {key: value for key, value in payload.items() if key != "work_items"}
            payload["work_items"] = work_items
            history = current.get("progress_events")
            if not isinstance(history, list):
                history = []
            history.append({"work_item_id": self.work_item_id, "event": event, "phase": phase, "at": now, "details": details})
            payload["progress_events"] = history[-100:]
            self._store.write(self.trace_id, payload)

    def bind_agent(self, agent: Any) -> None:
        self._bound_agent_id = str(getattr(agent, "id", "")) or None
        _bind_tracker(self)

    def unbind_agent(self) -> None:
        _unbind_tracker(self)
        self._bound_agent_id = None

    def llm_started(self, event: Any) -> None:
        self._llm_calls += 1
        self._llm_state = "started"
        self._llm_call_id = getattr(event, "call_id", None)
        self.update("llm_streaming" if getattr(event, "stream", False) else "llm_request", "llm_request_started", call_id=getattr(event, "call_id", None))

    def llm_chunk(self, event: Any) -> None:
        content = str(getattr(event, "chunk", "") or "")
        self._chunks += 1
        self._bytes += len(content.encode("utf-8"))
        self._llm_state = "streaming"
        self.update("llm_streaming", "llm_chunk_received", bytes=len(content.encode("utf-8")), chunks=self._chunks)

    def llm_completed(self, event: Any) -> None:
        if self._llm_state in {"completed", "failed", "cancelled"}:
            return
        self._llm_state = "completed"
        self._llm_terminal_at = _now()
        self.update("llm_completed", "llm_completed", call_id=getattr(event, "call_id", self._llm_call_id))

    def llm_failed(self, event: Any) -> None:
        if self._llm_state in {"completed", "failed", "cancelled"}:
            return
        self._llm_state = "failed"
        self._llm_terminal_at = _now()
        self.update("llm_failed", "llm_failed", error=str(getattr(event, "error", "unknown")))

    def llm_completed_from_agent_return(self) -> None:
        """Agent execution returned normally; this is the non-stream terminal signal."""
        self.llm_completed(type("Event", (), {"call_id": self._llm_call_id})())

    def tool_started(self, tool_name: str) -> None:
        self._tool_calls += 1
        self.update("running_tool", "tool_started", tool_name=tool_name)

    def tool_completed(self, tool_name: str, *, success: bool = True) -> None:
        self.update("running_tool", "tool_completed", tool_name=tool_name, success=success)

    def completed(self) -> None:
        self.update("completed", "agent_completed")
        self.update("completed", "work_item_completed")

    def failed(self, error: str) -> None:
        self.update("failed", "agent_failed", error=error)
        self.update("failed", "work_item_failed", error=error)

    def cancel_requested(self) -> bool:
        return self._store.cancel_requested(self.trace_id)


_trackers: dict[str, ProgressTracker] = {}
_trackers_lock = RLock()
_handlers_registered = False


def _tracker_for(event: Any) -> ProgressTracker | None:
    agent_id = str(getattr(event, "agent_id", "") or "")
    with _trackers_lock:
        tracker = _trackers.get(agent_id)
        if tracker is not None:
            return tracker
        # Some CrewAI integrations omit ``from_agent`` on provider callbacks.
        # A single active tracker is still unambiguous within an isolated Worker.
        return next(iter(_trackers.values()), None) if len(_trackers) == 1 else None


def _on_llm_started(_source: Any, event: LLMCallStartedEvent) -> None:
    tracker = _tracker_for(event)
    if tracker is not None:
        tracker.llm_started(event)


def _on_llm_chunk(_source: Any, event: LLMStreamChunkEvent) -> None:
    tracker = _tracker_for(event)
    if tracker is not None:
        tracker.llm_chunk(event)


def _on_llm_completed(_source: Any, event: LLMCallCompletedEvent) -> None:
    tracker = _tracker_for(event)
    if tracker is not None:
        tracker.llm_completed(event)


def _on_llm_failed(_source: Any, event: LLMCallFailedEvent) -> None:
    tracker = _tracker_for(event)
    if tracker is not None:
        tracker.llm_failed(event)


def _ensure_handlers() -> None:
    global _handlers_registered
    registered_types = getattr(crewai_event_bus, "_sync_handlers", {})
    if _handlers_registered and all(
        event_type in registered_types
        for event_type in (
            LLMCallStartedEvent,
            LLMStreamChunkEvent,
            LLMCallCompletedEvent,
            LLMCallFailedEvent,
        )
    ):
        return
    crewai_event_bus.on(LLMCallStartedEvent)(_on_llm_started)
    crewai_event_bus.on(LLMStreamChunkEvent)(_on_llm_chunk)
    crewai_event_bus.on(LLMCallCompletedEvent)(_on_llm_completed)
    crewai_event_bus.on(LLMCallFailedEvent)(_on_llm_failed)
    _handlers_registered = True


def _bind_tracker(tracker: ProgressTracker) -> None:
    _ensure_handlers()
    if tracker._bound_agent_id:
        with _trackers_lock:
            _trackers[tracker._bound_agent_id] = tracker


def _unbind_tracker(tracker: ProgressTracker) -> None:
    if tracker._bound_agent_id:
        with _trackers_lock:
            if _trackers.get(tracker._bound_agent_id) is tracker:
                _trackers.pop(tracker._bound_agent_id, None)


def phase_idle_timeout(phase: str) -> float:
    import os

    defaults = {
        "llm_streaming": 120.0,
        "llm_request": 120.0,
        "running_tool": 300.0,
        "running_sandbox": 600.0,
    }
    key = "PROJECTOS_IDLE_" + phase.upper() + "_SECONDS"
    try:
        return max(1.0, float(os.environ.get(key, defaults.get(phase, 300.0))))
    except ValueError:
        return defaults.get(phase, 300.0)


def idle_for(payload: dict[str, Any] | None) -> float | None:
    return _seconds_since(str(payload.get("last_progress_at"))) if payload else None


def heartbeat_for(payload: dict[str, Any] | None) -> float | None:
    return _seconds_since(str(payload.get("heartbeat_at"))) if payload else None

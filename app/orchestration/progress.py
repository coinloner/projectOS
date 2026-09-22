"""ProjectOS v3 分层运行监控。

监控快照只保存当前事实，不复制事件历史、不保存 prompt/模型正文，也不把
WorkItem 镜像到 run 顶层。历史审计由 TraceStore.events.jsonl 负责。

层级：
- run: 一次 Trace 的总体生命周期和 Worker 进程观察；
- batches: 一轮并行 WorkItem 的 fan-out/fan-in 屏障；
- work_items: 单个节点及其 LLM 活动。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import Any, Mapping

from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import (
    LLMCallCompletedEvent,
    LLMCallFailedEvent,
    LLMCallStartedEvent,
    LLMStreamChunkEvent,
)


MONITOR_SCHEMA_VERSION = 3
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


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text[:limit] or None


def _bounded_strings(value: object, count: int, length: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    for item in value:
        text = _bounded_text(item, length)
        if text and text not in result:
            result.append(text)
        if len(result) >= count:
            break
    return tuple(result)


class ExecutionLifecycle(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    RETRYING = "retrying"
    TERMINAL = "terminal"


class ExecutionActivity(str, Enum):
    DISPATCH = "dispatch"
    WORKER = "worker"
    LLM = "llm"
    TOOL = "tool"
    ARTIFACT = "artifact"
    SANDBOX = "sandbox"
    INTEGRATION = "integration"
    APPROVAL = "approval"


class ExecutionOutcome(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ProgressSignalKind(str, Enum):
    TRANSPORT = "transport"
    SEMANTIC = "semantic"
    CONTROL = "control"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True)
class ProgressEvent:
    """进程内事件对象；持久化历史由 TraceStore 负责。"""

    sequence: int
    signal: ProgressSignalKind
    lifecycle: ExecutionLifecycle
    activity: ExecutionActivity
    event_type: str
    created_at: str
    work_item_id: str
    summary: str | None = None
    required_action: str | None = None
    evidence_refs: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "signal": self.signal.value,
            "lifecycle": self.lifecycle.value,
            "activity": self.activity.value,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "work_item_id": self.work_item_id,
            "summary": self.summary,
            "required_action": self.required_action,
            "evidence_refs": list(self.evidence_refs),
            "details": _safe_details(self.details),
        }


@dataclass(frozen=True)
class WorkingState:
    """仅供当前进程恢复提示使用，不再写入监控快照。"""

    objective: str = ""
    attempt: int = 1
    completed_actions: tuple[str, ...] = ()
    durable_output_refs: tuple[str, ...] = ()
    open_actions: tuple[str, ...] = ()
    current_action: str | None = None
    next_action: str | None = None
    last_error: str | None = None
    contract_digest: str | None = None

    @classmethod
    def from_dict(cls, payload: object) -> "WorkingState":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            objective=_bounded_text(payload.get("objective"), 500) or "",
            attempt=max(1, _safe_int(payload.get("attempt"), 1)),
            completed_actions=_bounded_strings(payload.get("completed_actions"), 20, 240),
            durable_output_refs=_bounded_strings(payload.get("durable_output_refs"), 100, 500),
            open_actions=_bounded_strings(payload.get("open_actions"), 20, 240),
            current_action=_bounded_text(payload.get("current_action"), 240),
            next_action=_bounded_text(payload.get("next_action"), 240),
            last_error=_bounded_text(payload.get("last_error"), 500),
            contract_digest=_bounded_text(payload.get("contract_digest"), 64),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "attempt": self.attempt,
            "completed_actions": list(self.completed_actions),
            "durable_output_refs": list(self.durable_output_refs),
            "open_actions": list(self.open_actions),
            "current_action": self.current_action,
            "next_action": self.next_action,
            "last_error": self.last_error,
            "contract_digest": self.contract_digest,
        }


def _safe_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Persist bounded operational metadata only."""
    sensitive = {
        "arguments", "content", "error", "input", "output", "prompt",
        "reasoning", "request", "response", "result", "tool_arguments",
    }
    result: dict[str, Any] = {}
    for key, value in details.items():
        name = str(key)
        if name.lower() in sensitive:
            continue
        if value is None or isinstance(value, (bool, int, float, str)):
            result[name] = value
        elif isinstance(value, (list, tuple)):
            result[name] = [str(item)[:240] for item in value[:50]]
        elif isinstance(value, dict):
            result[name] = _safe_details(value)
    return result


def _published_refs(tool_name: str, result: str | None) -> tuple[str, ...]:
    value = result or ""
    import re

    staged = re.search(r"staged:[A-Za-z0-9_.:-]+", value)
    if staged and tool_name.startswith(("write_", "compile_")):
        return (staged.group(0),)
    candidate = re.search(r"(?:已创建|已复用)(?:架构|任务)候选:\s*([A-Za-z0-9_.-]+)", value)
    if candidate:
        return (f"candidate:{candidate.group(1)}",)
    commit = re.search(r"ChangeSet:\s*([A-Fa-f0-9]+)", value)
    if commit:
        return (f"changeset:{commit.group(1)}",)
    if tool_name in {"write_workspace_file", "write_test_file"}:
        return ("workspace-write",)
    if tool_name == "run_sandbox_check":
        return ("sandbox-evidence",)
    artifacts = {
        "save_requirement": "requirement",
        "save_architecture": "architecture",
        "save_implementation_contract": "architecture_contract",
        "compile_project_contract_from_designs": "architecture_contract",
        "save_environment": "environment",
        "save_implementation": "implementation",
        "save_tests": "tests",
        "save_review": "review",
    }
    return (f"published:{artifacts[tool_name]}:current",) if tool_name in artifacts else ()


class WorkerProgressStore:
    """持久化 v3 分层监控快照。"""

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
        if not isinstance(payload, dict) or payload.get("schema_version") != MONITOR_SCHEMA_VERSION:
            return None
        return payload

    def write(self, trace_id: str, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != MONITOR_SCHEMA_VERSION:
            raise ValueError("监控快照必须使用 schema_version=3")
        path = self.path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _empty(self, trace_id: str, *, now: str | None = None) -> dict[str, Any]:
        stamp = now or _now()
        return {
            "schema_version": MONITOR_SCHEMA_VERSION,
            "trace_id": trace_id,
            "run": {
                "lifecycle": ExecutionLifecycle.PENDING.value,
                "outcome": None,
                "activity": ExecutionActivity.DISPATCH.value,
                "event_type": "run_created",
                "sequence": 0,
                "started_at": stamp,
                "last_event_at": stamp,
                "last_meaningful_at": stamp,
                "heartbeat_at": None,
                "terminal_at": None,
                "worker_process_state": "not_started",
                "active_batch_id": None,
                "summary": None,
                "error_code": None,
                "evidence_refs": [],
                "counters": {"llm_calls": 0, "llm_chunks": 0, "llm_bytes": 0, "tool_calls": 0},
            },
            "batches": {},
            "work_items": {},
        }

    def _current(self, trace_id: str) -> dict[str, Any]:
        return self.read(trace_id) or self._empty(trace_id)

    def _commit(self, trace_id: str, payload: dict[str, Any]) -> None:
        self.write(trace_id, payload)

    def start_run(self, trace_id: str, *, event_type: str = "worker_started", worker_process_state: str = "running") -> None:
        with _progress_file_lock:
            payload = self._empty(trace_id)
            now = _now()
            payload["run"].update({
                "lifecycle": ExecutionLifecycle.RUNNING.value,
                "activity": ExecutionActivity.WORKER.value,
                "event_type": event_type,
                "sequence": 1,
                "started_at": now,
                "last_event_at": now,
                "last_meaningful_at": now,
                "heartbeat_at": now,
                "worker_process_state": worker_process_state,
            })
            self._commit(trace_id, payload)

    def ensure_run(self, trace_id: str) -> None:
        """Initialize direct GraphRunner executions that have no Worker bootstrap."""
        with _progress_file_lock:
            payload = self.read(trace_id)
            if payload is None:
                self.start_run(trace_id, event_type="graph_runner_started", worker_process_state="embedded")
                return
            run = payload["run"]
            if run.get("lifecycle") == ExecutionLifecycle.PENDING.value:
                now = _now()
                run.update({
                    "lifecycle": ExecutionLifecycle.RUNNING.value,
                    "activity": ExecutionActivity.DISPATCH.value,
                    "event_type": "graph_runner_started",
                    "sequence": _safe_int(run.get("sequence")) + 1,
                    "last_event_at": now,
                    "last_meaningful_at": now,
                })
                self._commit(trace_id, payload)

    def set_run_state(
        self,
        trace_id: str,
        *,
        lifecycle: ExecutionLifecycle,
        event_type: str,
        activity: ExecutionActivity = ExecutionActivity.WORKER,
        outcome: ExecutionOutcome | None = None,
        summary: str | None = None,
        error_code: str | None = None,
        worker_process_state: str | None = None,
    ) -> None:
        with _progress_file_lock:
            payload = self._current(trace_id)
            run = payload["run"]
            now = _now()
            run["lifecycle"] = lifecycle.value
            run["outcome"] = outcome.value if outcome else None
            run["activity"] = activity.value
            run["event_type"] = event_type
            run["sequence"] = _safe_int(run.get("sequence")) + 1
            run["last_event_at"] = now
            if lifecycle is not ExecutionLifecycle.TERMINAL:
                run["last_meaningful_at"] = now
            else:
                run["terminal_at"] = now
            if summary is not None:
                run["summary"] = _bounded_text(summary, 240)
            if error_code is not None:
                run["error_code"] = _bounded_text(error_code, 160)
            if worker_process_state is not None:
                run["worker_process_state"] = worker_process_state
            self._commit(trace_id, payload)

    def start_batch(self, trace_id: str, batch_id: str, *, wave_index: int, work_item_ids: tuple[str, ...]) -> None:
        with _progress_file_lock:
            payload = self._current(trace_id)
            now = _now()
            payload["batches"][batch_id] = {
                "batch_id": batch_id,
                "wave_index": wave_index,
                "lifecycle": ExecutionLifecycle.RUNNING.value,
                "outcome": None,
                "event_type": "batch_started",
                "sequence": 1,
                "started_at": now,
                "last_event_at": now,
                "terminal_at": None,
                "expected_work_item_ids": list(work_item_ids),
                "completed_work_item_ids": [],
                "failed_work_item_ids": [],
                "interrupted_work_item_ids": [],
                "root_failure_work_item_id": None,
                "barrier_state": "waiting_for_members",
                "summary": "并行批次开始",
                "error_code": None,
                "evidence_refs": [],
            }
            payload["run"]["active_batch_id"] = batch_id
            self._commit(trace_id, payload)

    def finish_batch(
        self,
        trace_id: str,
        batch_id: str,
        *,
        completed: tuple[str, ...] = (),
        failed: tuple[str, ...] = (),
        interrupted: tuple[str, ...] = (),
        outcome: ExecutionOutcome | None = None,
        root_failure_work_item_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with _progress_file_lock:
            payload = self._current(trace_id)
            batch = payload["batches"].get(batch_id)
            if not isinstance(batch, dict):
                return
            now = _now()
            final_outcome = outcome or (ExecutionOutcome.FAILED if failed else ExecutionOutcome.COMPLETED)
            batch.update({
                "lifecycle": ExecutionLifecycle.TERMINAL.value,
                "outcome": final_outcome.value,
                "event_type": (
                    "batch_completed"
                    if final_outcome is ExecutionOutcome.COMPLETED
                    else "batch_blocked"
                    if final_outcome is ExecutionOutcome.BLOCKED
                    else "batch_interrupted"
                    if final_outcome in {ExecutionOutcome.CANCELLED, ExecutionOutcome.INTERRUPTED}
                    else "batch_failed"
                ),
                "sequence": _safe_int(batch.get("sequence")) + 1,
                "last_event_at": now,
                "terminal_at": now,
                "completed_work_item_ids": list(completed),
                "failed_work_item_ids": list(failed),
                "interrupted_work_item_ids": list(interrupted),
                "root_failure_work_item_id": root_failure_work_item_id,
                "barrier_state": (
                    "released"
                    if final_outcome is ExecutionOutcome.COMPLETED
                    else "waiting_for_approval"
                    if final_outcome is ExecutionOutcome.BLOCKED
                    else "blocked"
                ),
                "error_code": error_code,
            })
            if payload["run"].get("active_batch_id") == batch_id:
                payload["run"]["active_batch_id"] = None
            self._commit(trace_id, payload)

    def record_work_item_state(
        self,
        trace_id: str,
        work_item_id: str,
        agent_id: str,
        *,
        lifecycle: ExecutionLifecycle,
        event: str,
        summary: str,
        outcome: ExecutionOutcome | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.record_work_item_activity(
            trace_id,
            work_item_id,
            agent_id,
            lifecycle=lifecycle,
            activity=ExecutionActivity.WORKER,
            event_type=event,
            signal=ProgressSignalKind.CONTROL,
            summary=summary,
            outcome=outcome,
            details=details,
        )

    def record_work_item_activity(
        self,
        trace_id: str,
        work_item_id: str,
        agent_id: str,
        *,
        lifecycle: ExecutionLifecycle,
        activity: ExecutionActivity,
        event_type: str,
        signal: ProgressSignalKind,
        summary: str | None = None,
        required_action: str | None = None,
        outcome: ExecutionOutcome | None = None,
        evidence_refs: tuple[str, ...] = (),
        durable: bool = False,
        details: dict[str, Any] | None = None,
        counters: dict[str, int] | None = None,
        llm: dict[str, Any] | None = None,
    ) -> None:
        with _progress_file_lock:
            payload = self._current(trace_id)
            now = _now()
            items = payload["work_items"]
            previous = dict(items.get(work_item_id) or {})
            item = {
                **previous,
                "work_item_id": work_item_id,
                "agent_id": agent_id,
                "lifecycle": lifecycle.value,
                "outcome": outcome.value if outcome else None,
                "activity": activity.value,
                "event_type": event_type,
                "sequence": _safe_int(previous.get("sequence")) + 1,
                "started_at": previous.get("started_at") or now,
                "last_event_at": now,
                "last_meaningful_at": previous.get("last_meaningful_at"),
                "terminal_at": previous.get("terminal_at"),
                "summary": _bounded_text(summary, 240) if summary is not None else previous.get("summary"),
                "required_action": _bounded_text(required_action, 240) if required_action is not None else previous.get("required_action"),
                "error_code": _bounded_text((details or {}).get("error_type") or (details or {}).get("failure_kind"), 160),
                "evidence_refs": list(dict.fromkeys((*previous.get("evidence_refs", []), *evidence_refs))) if durable else previous.get("evidence_refs", []),
                "counters": {**(previous.get("counters") or {}), **(counters or {})},
            }
            clocks = dict(previous.get("clocks") or {})
            if signal is ProgressSignalKind.TRANSPORT:
                clocks["transport_at"] = now
            if signal in {ProgressSignalKind.SEMANTIC, ProgressSignalKind.CONTROL}:
                item["last_meaningful_at"] = now
            item["clocks"] = clocks
            if lifecycle is ExecutionLifecycle.TERMINAL:
                item["terminal_at"] = now
                item["required_action"] = None
            if llm is not None:
                item["llm"] = dict(llm)
            items[work_item_id] = item
            run = payload["run"]
            run["last_event_at"] = now
            run["sequence"] = _safe_int(run.get("sequence")) + 1
            if signal in {ProgressSignalKind.SEMANTIC, ProgressSignalKind.CONTROL}:
                run["last_meaningful_at"] = now
            aggregate = run.get("counters") or {}
            for key, value in (counters or {}).items():
                aggregate[key] = sum(_safe_int(v.get("counters", {}).get(key)) for v in items.values() if isinstance(v, dict))
            run["counters"] = aggregate
            self._commit(trace_id, payload)

    def finalize_run(
        self,
        trace_id: str,
        *,
        outcome: ExecutionOutcome,
        event: str,
        summary: str,
        details: dict[str, Any] | None = None,
        root_work_item_id: str | None = None,
    ) -> tuple[str, ...]:
        with _progress_file_lock:
            payload = self._current(trace_id)
            now = _now()
            run = payload["run"]
            run.update({
                "lifecycle": ExecutionLifecycle.TERMINAL.value,
                "outcome": outcome.value,
                "activity": ExecutionActivity.WORKER.value,
                "event_type": event,
                "sequence": _safe_int(run.get("sequence")) + 1,
                "last_event_at": now,
                "terminal_at": now,
                "worker_process_state": "terminated",
                "summary": _bounded_text(summary, 240),
                "error_code": _bounded_text((details or {}).get("error_code") or (details or {}).get("failure_kind") or event, 160),
            })
            active: list[str] = []
            for item_id, item in payload["work_items"].items():
                if not isinstance(item, dict) or item.get("lifecycle") not in {"running", "retrying", "waiting"}:
                    continue
                active.append(item_id)
                item["lifecycle"] = ExecutionLifecycle.TERMINAL.value
                item["outcome"] = outcome.value if item_id == root_work_item_id or root_work_item_id is None else ExecutionOutcome.INTERRUPTED.value
                item["event_type"] = (
                    "work_item_interrupted"
                    if item["outcome"] == ExecutionOutcome.INTERRUPTED.value
                    else "work_item_completed"
                    if outcome is ExecutionOutcome.COMPLETED
                    else "work_item_blocked"
                    if outcome is ExecutionOutcome.BLOCKED
                    else "work_item_cancelled"
                    if outcome is ExecutionOutcome.CANCELLED
                    else "work_item_failed"
                )
                item["activity"] = ExecutionActivity.WORKER.value
                item["last_event_at"] = now
                item["terminal_at"] = now
                item["required_action"] = None
                item["error_code"] = run["error_code"]
                llm = item.get("llm")
                if isinstance(llm, dict) and llm.get("state") not in {"completed", "failed", "cancelled"}:
                    item["llm"] = {
                        **llm,
                        "state": "cancelled" if outcome is ExecutionOutcome.CANCELLED else "failed",
                        "terminal_at": now,
                    }
            batch_id = run.get("active_batch_id")
            if batch_id and isinstance(payload["batches"].get(batch_id), dict):
                batch = payload["batches"][batch_id]
                batch["lifecycle"] = ExecutionLifecycle.TERMINAL.value
                batch["outcome"] = outcome.value
                batch["event_type"] = "batch_interrupted"
                batch["terminal_at"] = now
                batch["barrier_state"] = "blocked"
                batch["root_failure_work_item_id"] = root_work_item_id
                batch["interrupted_work_item_ids"] = [item_id for item_id in active if item_id != root_work_item_id]
                batch["failed_work_item_ids"] = [root_work_item_id] if root_work_item_id else active
                run["active_batch_id"] = None
            self._commit(trace_id, payload)
            return tuple(active)

    def record_planning_state(
        self,
        trace_id: str,
        *,
        lifecycle: ExecutionLifecycle,
        event: str,
        summary: str,
        activity: ExecutionActivity = ExecutionActivity.LLM,
        outcome: ExecutionOutcome | None = None,
        attempt: int | None = None,
        details: dict[str, Any] | None = None,
        signal: ProgressSignalKind = ProgressSignalKind.CONTROL,
        counter_increments: dict[str, int] | None = None,
    ) -> None:
        with _progress_file_lock:
            payload = self._current(trace_id)
            run = payload["run"]
            now = _now()
            run.update({
                "lifecycle": lifecycle.value,
                "outcome": outcome.value if outcome else None,
                "activity": activity.value,
                "event_type": event,
                "sequence": _safe_int(run.get("sequence")) + 1,
                "last_event_at": now,
                "summary": _bounded_text(summary, 240),
                "error_code": _bounded_text((details or {}).get("failure_kind"), 160),
            })
            if signal in {ProgressSignalKind.SEMANTIC, ProgressSignalKind.CONTROL}:
                run["last_meaningful_at"] = now
            counters = run.get("counters") or {}
            for key, value in (counter_increments or {}).items():
                counters[key] = _safe_int(counters.get(key)) + _safe_int(value)
            if event == "planning_attempt_started":
                counters["llm_calls"] = _safe_int(counters.get("llm_calls")) + 1
            run["counters"] = counters
            self._commit(trace_id, payload)

    def request_cancel(self, trace_id: str, *, reason: str) -> None:
        self._write_control(trace_id, "cancel", reason)

    def request_provider_stall(self, trace_id: str, *, reason: str) -> None:
        self._write_control(trace_id, "provider_stalled", reason)

    def _write_control(self, trace_id: str, action: str, reason: str) -> None:
        path = self.control_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"trace_id": trace_id, "action": action, "reason": reason, "requested_at": _now()}, ensure_ascii=False) + "\n", encoding="utf-8")

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
        path = self.lease_path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
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
            stream.write(json.dumps({"trace_id": trace_id, "pid": os.getpid(), "claimed_at": _now()}) + "\n")
        return True

    def release_worker(self, trace_id: str) -> None:
        path = self.lease_path(trace_id)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if int(current.get("pid", 0)) != os.getpid():
                return
            path.unlink()
        except (OSError, ValueError, TypeError):
            return

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
    """只更新 run.heartbeat_at，不改变业务生命周期。"""

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
            with _progress_file_lock:
                payload = self._store.read(self._trace_id)
                if not payload:
                    continue
                payload["run"]["heartbeat_at"] = _now()
                self._store.write(self._trace_id, payload)


@dataclass
class ProgressTracker:
    """单个 WorkItem 的实时执行观察器。"""

    project_path: str
    trace_id: str
    work_item_id: str
    agent_id: str
    objective: str = ""
    attempt: int = 1
    contract_digest: str | None = None
    recovery_context: dict[str, object] | None = None
    _store: WorkerProgressStore = field(init=False)
    _lock: RLock = field(default_factory=RLock, init=False)
    _chunks: int = field(default=0, init=False)
    _bytes: int = field(default=0, init=False)
    _llm_calls: int = field(default=0, init=False)
    _tool_calls: int = field(default=0, init=False)
    _llm_state: str = field(default="idle", init=False)
    _llm_call_id: str | None = field(default=None, init=False)
    _llm_terminal_at: str | None = field(default=None, init=False)
    _pending_chunk_count: int = field(default=0, init=False)
    _pending_chunk_bytes: int = field(default=0, init=False)
    _last_chunk_flush_monotonic: float = field(default_factory=time.monotonic, init=False)
    _bound_agent_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._store = WorkerProgressStore(self.project_path)
        self._store.record_work_item_activity(
            self.trace_id,
            self.work_item_id,
            self.agent_id,
            lifecycle=ExecutionLifecycle.RETRYING if self.attempt > 1 else ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.WORKER,
            event_type="work_item_started",
            signal=ProgressSignalKind.CONTROL,
            summary="工作项开始执行" if self.attempt == 1 else "工作项开始重试",
        )

    def update(self, activity_hint: str, event: str, **details: Any) -> None:
        """将调用点的活动提示转换成规范 activity；不持久化阶段字段。"""
        activity = ExecutionActivity.WORKER
        signal = ProgressSignalKind.CONTROL
        if event.startswith("llm_"):
            activity = ExecutionActivity.LLM
            signal = ProgressSignalKind.TRANSPORT if event != "llm_failed" else ProgressSignalKind.CONTROL
        elif event.startswith("tool_"):
            activity = ExecutionActivity.TOOL
            signal = ProgressSignalKind.SEMANTIC if event == "tool_started" else ProgressSignalKind.CONTROL
        elif event.startswith("file_"):
            activity = ExecutionActivity.ARTIFACT
            signal = ProgressSignalKind.SEMANTIC if event == "file_write_started" else ProgressSignalKind.CONTROL
        elif "sandbox" in activity_hint or "sandbox" in event:
            activity = ExecutionActivity.SANDBOX
        elif "integrat" in event:
            activity = ExecutionActivity.INTEGRATION
        self.emit(
            signal=signal,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=activity,
            event=event,
            summary=_summary_for_event(event, details),
            evidence_refs=_evidence_refs_for_event(self.trace_id, self.work_item_id, event, details),
            durable=event in {"tool_completed", "file_write_succeeded", "file_write_idempotent", "changeset_created"},
            details=details,
        )

    def emit(
        self,
        *,
        signal: ProgressSignalKind,
        lifecycle: ExecutionLifecycle,
        activity: ExecutionActivity,
        event: str,
        summary: str | None = None,
        next_action: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        durable: bool = False,
        outcome: ExecutionOutcome | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        counters = {
            "llm_calls": self._llm_calls,
            "llm_chunks": self._chunks,
            "llm_bytes": self._bytes,
            "tool_calls": self._tool_calls,
        }
        llm = {
            "state": self._llm_state,
            "call_id": self._llm_call_id,
            "terminal_at": self._llm_terminal_at,
        }
        self._store.record_work_item_activity(
            self.trace_id,
            self.work_item_id,
            self.agent_id,
            lifecycle=lifecycle,
            activity=activity,
            event_type=event,
            signal=signal,
            summary=summary,
            required_action=next_action,
            outcome=outcome,
            evidence_refs=evidence_refs,
            durable=durable,
            details=details,
            counters=counters,
            llm=llm,
        )

    def report_semantic(
        self,
        *,
        work_stage: str,
        summary: str,
        next_action: str,
        confirmed_refs: tuple[str, ...] = (),
    ) -> bool:
        payload = self._store.read(self.trace_id) or {}
        item = payload.get("work_items", {}).get(self.work_item_id, {})
        if item.get("summary") == summary and item.get("required_action") == next_action:
            return False
        self.emit(
            signal=ProgressSignalKind.SEMANTIC,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="agent_progress_reported",
            summary=summary,
            next_action=next_action,
            evidence_refs=confirmed_refs,
            details={"work_stage": work_stage, "verification": "pending"},
        )
        return True

    def resume_prompt(self) -> str:
        """Return bounded, ledger-derived recovery guidance for this attempt.

        This deliberately does not read or write worker-status.json. Monitoring
        remains current-state only; the caller injects the already-authorized
        RetryLedger summary for this WorkItem when a checkpoint is resumed.
        """
        if not self.recovery_context:
            return ""
        try:
            return json.dumps(self.recovery_context, ensure_ascii=False, sort_keys=True)[:2_000]
        except (TypeError, ValueError):
            return ""

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
        self._llm_terminal_at = None
        self.update("llm_streaming", "llm_request_started", call_id=self._llm_call_id)

    def llm_chunk(self, event: Any) -> None:
        content = str(getattr(event, "chunk", "") or "")
        self._chunks += 1
        self._bytes += len(content.encode("utf-8"))
        self._pending_chunk_count += 1
        self._pending_chunk_bytes += len(content.encode("utf-8"))
        self._llm_state = "streaming"
        if (
            self._pending_chunk_count >= 64
            or self._chunks == self._pending_chunk_count
            or time.monotonic() - self._last_chunk_flush_monotonic >= 1.0
        ):
            self._flush_llm_chunks()

    def llm_completed(self, event: Any) -> None:
        if self._llm_state in {"completed", "failed", "cancelled"}:
            return
        self._flush_llm_chunks()
        self._llm_state = "completed"
        self._llm_terminal_at = _now()
        self.update("llm_completed", "llm_completed", call_id=getattr(event, "call_id", self._llm_call_id))

    def llm_failed(self, event: Any) -> None:
        if self._llm_state in {"completed", "failed", "cancelled"}:
            return
        self._flush_llm_chunks()
        self._llm_state = "failed"
        self._llm_terminal_at = _now()
        self.update("llm_failed", "llm_failed", error_type=_error_type(getattr(event, "error", "unknown")))

    def llm_completed_from_agent_return(self) -> None:
        self.llm_completed(type("Complete", (), {"call_id": self._llm_call_id})())

    def tool_started(self, tool_name: str) -> None:
        self._tool_calls += 1
        self.update("running_tool", "tool_started", tool_name=tool_name)

    def tool_completed(self, tool_name: str, *, success: bool = True, result: str | None = None) -> None:
        self.update("running_tool", "tool_completed" if success else "tool_failed", tool_name=tool_name, result=result, success=success)

    def record_outcome(self, outcome: ExecutionOutcome, *, summary: str | None = None) -> None:
        self.emit(
            signal=ProgressSignalKind.CONTROL,
            lifecycle=ExecutionLifecycle.TERMINAL,
            activity=ExecutionActivity.WORKER,
            event=f"work_item_{outcome.value}",
            summary=summary or f"工作项已{outcome.value}",
            outcome=outcome,
        )

    def completed(self) -> None:
        self.emit(
            signal=ProgressSignalKind.CONTROL,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.WORKER,
            event="agent_returned",
            summary="Agent 已返回，等待控制面验证",
        )

    def failed(self, error: str) -> None:
        self.emit(
            signal=ProgressSignalKind.CONTROL,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="agent_failed",
            summary="Agent 调用失败，等待控制面处理",
            details={"error_type": _error_type(error)},
        )

    def cancel_requested(self) -> bool:
        return self._store.cancel_requested(self.trace_id)

    def _flush_llm_chunks(self) -> None:
        if self._pending_chunk_count <= 0:
            return
        chunks, bytes_count = self._pending_chunk_count, self._pending_chunk_bytes
        self._pending_chunk_count = 0
        self._pending_chunk_bytes = 0
        self._last_chunk_flush_monotonic = time.monotonic()
        self.update(
            "llm_streaming",
            "llm_chunk_batch_received",
            batch_chunks=chunks,
            batch_bytes=bytes_count,
        )


def _error_type(error: object) -> str:
    if isinstance(error, BaseException):
        return type(error).__name__
    return str(error).split(":", 1)[0][:160] or "unknown"


def _summary_for_event(event: str, details: Mapping[str, Any]) -> str | None:
    tool = _bounded_text(details.get("tool_name"), 120)
    path = _bounded_text(details.get("path"), 300)
    values = {
        "work_item_started": "工作项开始执行",
        "llm_request_started": "模型请求已发起",
        "llm_completed": "模型请求已完成",
        "llm_failed": "模型请求失败",
        "llm_chunk_batch_received": "模型收到流式分片",
        "tool_started": f"开始执行工具 {tool}" if tool else "开始执行工具",
        "tool_completed": f"工具 {tool} 已完成" if tool else "工具执行完成",
        "tool_failed": f"工具 {tool} 执行失败" if tool else "工具执行失败",
        "file_write_started": f"开始写入 {path}" if path else "开始写入文件",
        "file_write_succeeded": f"文件写入成功 {path}" if path else "文件写入成功",
        "file_write_idempotent": f"文件无需变更 {path}" if path else "文件无需变更",
        "changeset_created": "ChangeSet 已创建",
        "agent_returned": "Agent 已返回，等待控制面验证",
        "agent_failed": "Agent 执行失败",
    }
    return values.get(event)


def _evidence_refs_for_event(trace_id: str, work_item_id: str, event: str, details: Mapping[str, Any]) -> tuple[str, ...]:
    if event == "changeset_created" and details.get("commit"):
        return (f"changeset:{trace_id}:{work_item_id}:{details['commit']}",)
    return _published_refs(str(details.get("tool_name", "")), str(details.get("result", ""))) if event == "tool_completed" else ()


# CrewAI event bridge -------------------------------------------------------
_trackers: dict[str, ProgressTracker] = {}
_trackers_lock = RLock()
_handlers_registered = False
_planning_stream_tracker: ContextVar["PlanningStreamTracker | None"] = ContextVar("projectos_planning_stream_tracker", default=None)


def _tracker_for(event: Any) -> ProgressTracker | None:
    agent_id = str(getattr(event, "agent_id", "") or "")
    with _trackers_lock:
        if agent_id in _trackers:
            return _trackers[agent_id]
        return next(iter(_trackers.values()), None) if len(_trackers) == 1 else None


def _on_llm_started(_source: Any, event: LLMCallStartedEvent) -> None:
    planner = _planning_stream_tracker.get()
    if planner is not None and not getattr(event, "agent_id", None):
        planner.llm_started(event)
    else:
        tracker = _tracker_for(event)
        if tracker:
            tracker.llm_started(event)


def _on_llm_chunk(_source: Any, event: LLMStreamChunkEvent) -> None:
    planner = _planning_stream_tracker.get()
    if planner is not None and not getattr(event, "agent_id", None):
        planner.llm_chunk(event)
    else:
        tracker = _tracker_for(event)
        if tracker:
            tracker.llm_chunk(event)


def _on_llm_completed(_source: Any, event: LLMCallCompletedEvent) -> None:
    planner = _planning_stream_tracker.get()
    if planner is not None and not getattr(event, "agent_id", None):
        planner.llm_completed(event)
    else:
        tracker = _tracker_for(event)
        if tracker:
            tracker.llm_completed(event)


def _on_llm_failed(_source: Any, event: LLMCallFailedEvent) -> None:
    planner = _planning_stream_tracker.get()
    if planner is not None and not getattr(event, "agent_id", None):
        planner.llm_failed(event)
    else:
        tracker = _tracker_for(event)
        if tracker:
            tracker.llm_failed(event)


def _ensure_handlers() -> None:
    global _handlers_registered
    if _handlers_registered:
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


@dataclass
class PlanningStreamTracker:
    store: WorkerProgressStore
    trace_id: str
    attempt: int
    _pending_chunks: int = field(default=0, init=False)
    _pending_bytes: int = field(default=0, init=False)
    _total_chunks: int = field(default=0, init=False)
    _terminal: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)

    def llm_started(self, event: Any) -> None:
        if self._closed or self._terminal:
            return
        self.store.record_planning_state(
            self.trace_id,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="planning_llm_started",
            summary="规划模型请求已发起",
            attempt=self.attempt,
            signal=ProgressSignalKind.TRANSPORT,
            counter_increments={"llm_calls": 1},
        )

    def llm_chunk(self, event: Any) -> None:
        if self._closed or self._terminal:
            return
        value = str(getattr(event, "chunk", "") or "")
        self._pending_chunks += 1
        self._pending_bytes += len(value.encode("utf-8"))
        self._total_chunks += 1
        if self._pending_chunks >= 64 or self._total_chunks == self._pending_chunks:
            self.flush_chunks()

    def flush_chunks(self) -> None:
        if not self._pending_chunks:
            return
        chunks, bytes_count = self._pending_chunks, self._pending_bytes
        self._pending_chunks = self._pending_bytes = 0
        self.store.record_planning_state(
            self.trace_id,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="planning_llm_chunk_batch",
            summary="规划模型收到流式分片",
            attempt=self.attempt,
            signal=ProgressSignalKind.TRANSPORT,
            counter_increments={"llm_chunks": chunks, "llm_bytes": bytes_count},
        )

    def llm_completed(self, event: Any) -> None:
        if self._closed or self._terminal:
            return
        self.flush_chunks()
        self._terminal = True
        self.store.record_planning_state(
            self.trace_id,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="planning_llm_completed",
            summary="规划模型请求已完成",
            attempt=self.attempt,
            signal=ProgressSignalKind.CONTROL,
        )

    def llm_failed(self, event: Any) -> None:
        if self._closed or self._terminal:
            return
        self.flush_chunks()
        self._terminal = True
        self.store.record_planning_state(
            self.trace_id,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.LLM,
            event="planning_llm_failed",
            summary="规划模型请求失败",
            attempt=self.attempt,
            signal=ProgressSignalKind.CONTROL,
            details={"error_type": _error_type(getattr(event, "error", "unknown"))},
        )

    def close(self) -> None:
        if not self._closed:
            self.flush_chunks()
            self._closed = True


@contextmanager
def track_planning_stream(store: WorkerProgressStore, trace_id: str, *, attempt: int):
    tracker = PlanningStreamTracker(store, trace_id, attempt)
    token = _planning_stream_tracker.set(tracker)
    try:
        yield tracker
    finally:
        tracker.close()
        _planning_stream_tracker.reset(token)


# Read-only monitoring helpers ---------------------------------------------

def meaningful_idle_for(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    if "run" in payload:
        return _seconds_since((payload.get("run") or {}).get("last_meaningful_at"))
    return _seconds_since(payload.get("last_meaningful_at"))


def transport_idle_for(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    clocks = payload.get("clocks") or {}
    return _seconds_since(clocks.get("transport_at"))


def heartbeat_for(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    run = payload.get("run") if isinstance(payload.get("run"), dict) else payload
    return _seconds_since(run.get("heartbeat_at"))


def transport_stall_timeout(activity: str) -> float:
    defaults = {"llm": 60.0, "tool": 300.0, "artifact": 300.0, "sandbox": 300.0, "integration": 300.0}
    env_name = "PROJECTOS_TRANSPORT_STALL_" + activity.upper() + "_SECONDS"
    try:
        return max(1.0, float(os.environ.get(env_name, defaults.get(activity, 300.0))))
    except ValueError:
        return defaults.get(activity, 300.0)


def semantic_stall_timeout(activity: str) -> float:
    key = activity.upper()
    try:
        return max(1.0, float(os.environ.get(f"PROJECTOS_SEMANTIC_STALL_{key}_SECONDS", os.environ.get(f"PROJECTOS_IDLE_RUNNING_{key}_SECONDS", "60"))))
    except ValueError:
        return 60.0


def canonical_progress(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """返回不含旧字段的 v3 API 监控视图。"""
    if payload is None:
        return None
    if payload.get("schema_version") != MONITOR_SCHEMA_VERSION:
        raise ValueError("不支持旧版监控快照；请重新启动当前 Trace")
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    run = result.get("run") or {}
    work_items = result.get("work_items") or {}
    batches = result.get("batches") or {}
    active = [item for item in work_items.values() if isinstance(item, dict) and item.get("lifecycle") in {"running", "retrying"}]
    waiting = [item for item in work_items.values() if isinstance(item, dict) and item.get("lifecycle") == "waiting"]
    completed = [item for item in work_items.values() if isinstance(item, dict) and item.get("outcome") == "completed"]
    failed = [item for item in work_items.values() if isinstance(item, dict) and item.get("outcome") in {"failed", "blocked"}]
    result["summary"] = {
        "lifecycle": run.get("lifecycle"),
        "outcome": run.get("outcome"),
        "active_work_item_ids": [item.get("work_item_id") for item in active],
        "waiting_work_item_ids": [item.get("work_item_id") for item in waiting],
        "completed_work_item_ids": [item.get("work_item_id") for item in completed],
        "failed_work_item_ids": [item.get("work_item_id") for item in failed],
        "active_batch_id": run.get("active_batch_id"),
        "worker_process_state": run.get("worker_process_state"),
    }
    for item in work_items.values():
        if isinstance(item, dict):
            item["meaningful_idle_seconds"] = meaningful_idle_for(item)
            item["transport_idle_seconds"] = transport_idle_for(item)
    result["run"]["meaningful_idle_seconds"] = meaningful_idle_for(result)
    result["run"]["heartbeat_idle_seconds"] = heartbeat_for(result)
    return result

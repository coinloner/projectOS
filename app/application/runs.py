"""HTTP、CLI 等入口共用的运行应用服务。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import multiprocessing
import os
import re
import time
import traceback
from threading import Lock
from typing import Callable
from uuid import uuid4

from app.bootstrap.runtime import ProjectOSContainer, build_container
from app.application.environment import EnvironmentProvisioner
from app.agent.result import normalize_capability
from app.orchestration.runner import GraphRunResult, GraphRunStatus
from app.orchestration.state import RunState
from app.orchestration.node_result import NodeResult, NodeStatus
from app.artifact.repository import ArtifactRef
from app.orchestration.retry import (
    FailureKind,
    FailureSignal,
    RecoveryAction,
    RetryLedger,
    RetryPolicy,
    RetryRecord,
    RetryScope,
)
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    ImplementationDesign,
    ModuleDesign,
    parse_design,
)
from app.execution_context import ExecutionMode
from app.orchestration.trace import TraceStore
from app.planner.service import PlannerFailure
from app.orchestration.progress import (
    ExecutionLifecycle,
    ExecutionOutcome,
    WorkerHeartbeat,
    WorkerProgressStore,
    heartbeat_for,
    meaningful_idle_for,
    semantic_stall_timeout,
    transport_stall_timeout,
    transport_idle_for,
)
from app.llm.config import LLMSelection


ContainerBuilder = Callable[[str], ProjectOSContainer]


_ARCHITECTURE_UNDECLARED_INTERFACE = re.compile(
    r"实现设计\s+([A-Za-z0-9._-]+)\s+引用了未声明的接口:\s*([A-Za-z0-9._-]+)"
)
_ARCHITECTURE_REQUIRED_PATH_NOT_OWNED = re.compile(
    r"实现单元\s+([A-Za-z0-9._-]+)\s+的 required_paths\s+必须属于 owned_files:\s*"
    r"([A-Za-z0-9._/-]+)"
)
_ARCHITECTURE_REQUIRED_FILE_NOT_OWNED = re.compile(
    r"ArchitectureBlueprint\.required_file_not_owned:\s*([A-Za-z0-9._/-]+)"
)
_ARCHITECTURE_MODULE_PARENT_MISMATCH = re.compile(
    r"模块设计\s+([A-Za-z0-9._-]+)\s+未引用当前总体蓝图"
)
_ARCHITECTURE_IMPLEMENTATION_PARENT_MISMATCH = re.compile(
    r"实现设计\s+([A-Za-z0-9._-]+)\s+未引用已存在的模块设计"
)
_ARCHITECTURE_UNKNOWN_IMPLEMENTATION_DEPENDENCY = re.compile(
    r"实现单元\s+([A-Za-z0-9._-]+)\s+依赖不存在的实现单元:\s*"
    r"([A-Za-z0-9._,\s-]+)"
)
_ARCHITECTURE_MODULE_PURPOSE_MISMATCH = re.compile(
    r"模块设计\s+([A-Za-z0-9._-]+)\s+的 purpose\s+必须与 Blueprint 一致"
)


def _format_error(error: BaseException) -> str:
    """Return a bounded, actionable error string for persisted Trace state.

    ``str(KeyError(0))`` is just ``"0"`` and made several Worker failures
    impossible to diagnose after the child process exited.  Persist the type,
    repr and a short traceback while keeping the payload bounded.
    """
    detail = f"{type(error).__name__}: {error!r}"
    stack = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if stack and stack.strip() != detail:
        detail = f"{detail}\n{stack}"
    return detail[-8_000:]


def _error_details(error: BaseException) -> dict[str, object]:
    return {
        "error_type": type(error).__name__,
        "error": repr(error),
        "traceback": "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )[-8_000:],
    }


@dataclass(frozen=True)
class StartedRun:
    trace_id: str
    plan_id: str
    workflow_id: str
    status: str


@dataclass(frozen=True)
class _StallObservation:
    work_item_id: str
    activity: str
    kind: str
    idle_seconds: float
    threshold: float
    progress_marker: str


@dataclass(frozen=True)
class _StallWindow:
    observed_at: float
    observation: _StallObservation


def _monitor_failure_kind(event: str, details: dict[str, object]) -> FailureKind | None:
    """Map Worker/watchdog facts to the same taxonomy used by GraphRunner."""
    if event == "provider_stall_timeout":
        return FailureKind.PROVIDER_STALL
    if event == "worker_timed_out":
        return FailureKind.WORKER_TIMEOUT
    if event == "worker_failed":
        source = str(details.get("source", ""))
        return (
            FailureKind.WORKER_BOOTSTRAP_FAILURE
            if source in {"worker_bootstrap", "worker_startup"}
            else FailureKind.WORKER_CRASH
        )
    # Cancellation is a control-plane termination, not a business failure and
    # must never consume retry budget or create a planner failure package.
    return None


def _record_monitor_retry(
    project_path: str,
    trace_id: str,
    *,
    event: str,
    summary: str,
    error: str,
    details: dict[str, object],
    active_batch_id: str | None,
    active_work_item_ids: tuple[str, ...],
    root_work_item_id: str | None,
    retry_policy: RetryPolicy | None = None,
) -> None:
    """Persist one normalized watchdog failure and its batch recovery relation."""
    kind = _monitor_failure_kind(event, details)
    if kind is None:
        return
    work_item_id = str(details.get("work_item_id") or root_work_item_id or "") or None
    input_digest = str(details.get("contract_digest") or "") or None
    signal = FailureSignal(
        kind=kind,
        summary=f"{summary}: {error}",
        input_digest=input_digest,
        retry_hint="resume_from_checkpoint" if kind is not FailureKind.WORKER_BOOTSTRAP_FAILURE else "repair_worker_bootstrap",
    )
    ledger = RetryLedger(project_path, trace_id)
    policy = retry_policy or RetryPolicy()
    scope = RetryScope.BATCH if active_batch_id else RetryScope.RUN
    subject_id = active_batch_id or trace_id
    item_retries = (
        ledger.budget_count_for_work_item(root_work_item_id)
        if root_work_item_id
        else 0
    )
    kind_retries = (
        ledger.budget_count_for_kind(root_work_item_id, kind)
        if root_work_item_id
        else 0
    )
    action = policy.action_for(
        signal,
        total_retries=ledger.budget_count(),
        item_retries=item_retries,
        kind_retries=kind_retries,
    )
    if scope is RetryScope.BATCH and action is RecoveryAction.RESUME:
        action = RecoveryAction.RETRY_BATCH
    interrupted = tuple(
        item_id
        for item_id in active_work_item_ids
        if item_id != root_work_item_id
    )
    record = RetryRecord(
        scope=scope,
        subject_id=subject_id,
        attempt=ledger.next_attempt(scope=scope, subject_id=subject_id),
        max_attempts=policy.max_attempts_for(kind),
        action=action,
        failure=signal,
        root_work_item_id=root_work_item_id,
        interrupted_work_item_ids=interrupted,
    )
    ledger.append(record)
    try:
        trace = TraceStore(project_path).load_trace(trace_id)
        from app.orchestration.trace import TraceContext

        context = TraceContext(
            requirement_id=str(trace["requirement_id"]),
            trace_id=trace_id,
            parent_trace_id=trace.get("parent_trace_id"),
        )
        TraceStore(project_path).record_event(
            context,
            active_batch_id or root_work_item_id or "worker",
            "retry_decision_recorded",
            details={"retry_record": record.as_dict()},
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        # The terminal Trace is still useful even when its audit event cannot
        # be appended during a process crash; retry-ledger remains authoritative.
        pass


def _finalize_external_run(
    project_path: str,
    trace_id: str,
    *,
    outcome: ExecutionOutcome,
    event: str,
    summary: str,
    error: str,
    details: dict[str, object] | None = None,
    retry_policy: RetryPolicy | None = None,
) -> tuple[str, ...]:
    """Close Trace, progress and open WorkItems after a forced Worker stop.

    A killed child cannot execute its normal ``GraphRunner``/tracker cleanup.
    This helper is deliberately usable from both the API-side watchdog and
    the child-side exception fallback, so every abnormal termination follows
    one control-plane protocol.
    """
    traces = TraceStore(project_path)
    try:
        payload = traces.load_trace(trace_id)
    except (FileNotFoundError, ValueError):
        return ()
    terminal = {
        GraphRunStatus.COMPLETED.value,
        GraphRunStatus.BLOCKED.value,
        GraphRunStatus.FAILED.value,
        GraphRunStatus.CANCELLED.value,
    }
    trace_status = str(payload.get("status", ""))
    trace_is_terminal = trace_status in terminal
    if trace_is_terminal:
        # GraphRunner may have persisted Trace=failed before a later exception
        # prevented the Worker snapshot from being closed.  Preserve that
        # authoritative outcome for the monitoring layer instead of trying to
        # move a completed Trace backwards.
        try:
            outcome = ExecutionOutcome(trace_status)
        except ValueError:
            trace_is_terminal = False

    store = WorkerProgressStore(project_path)
    before_progress = store.read(trace_id) or {}
    before_run = before_progress.get("run") if isinstance(before_progress, dict) else {}
    before_batch_id = (
        str(before_run.get("active_batch_id"))
        if isinstance(before_run, dict) and before_run.get("active_batch_id")
        else None
    )
    before_batch = (
        before_progress.get("batches", {}).get(before_batch_id, {})
        if before_batch_id and isinstance(before_progress.get("batches"), dict)
        else {}
    )
    before_items = (
        tuple(str(item_id) for item_id in before_batch.get("expected_work_item_ids", []))
        if isinstance(before_batch, dict)
        else ()
    )
    root_work_item_id = None
    if details:
        root_work_item_id = str(details.get("work_item_id") or "") or None
        if root_work_item_id is None:
            stalled = details.get("stalled_work_items")
            if isinstance(stalled, (list, tuple)) and stalled:
                root_work_item_id = str(stalled[0])
    # A wall-clock Worker timeout may fire before the parent has observed a
    # per-item stall window. Preserve a deterministic batch root so the retry
    # ledger can distinguish the causal member from interrupted siblings.
    if root_work_item_id is None and before_items:
        root_work_item_id = before_items[0]
    closed_items = store.finalize_run(
        trace_id,
        outcome=outcome,
        event=event,
        summary=summary,
        details=details,
        root_work_item_id=root_work_item_id,
    )
    if not trace_is_terminal and outcome is ExecutionOutcome.FAILED:
        _record_monitor_retry(
            project_path,
            trace_id,
            event=event,
            summary=summary,
            error=error,
            details=dict(details or {}),
            active_batch_id=before_batch_id,
            active_work_item_ids=tuple(closed_items) or before_items,
            root_work_item_id=root_work_item_id,
            retry_policy=retry_policy,
        )
    if not trace_is_terminal:
        from app.orchestration.trace import TraceContext

        context = TraceContext(
            requirement_id=str(payload["requirement_id"]),
            trace_id=trace_id,
            parent_trace_id=payload.get("parent_trace_id"),
        )
        for work_item_id in closed_items:
            item_event = (
                "work_item_completed"
                if outcome is ExecutionOutcome.COMPLETED
                else "work_item_blocked"
                if outcome is ExecutionOutcome.BLOCKED
                else "work_item_cancelled"
                if outcome is ExecutionOutcome.CANCELLED
                else "work_item_interrupted"
                if root_work_item_id is not None and work_item_id != root_work_item_id
                else "work_item_failed"
            )
            traces.record_event(
                context,
                work_item_id,
                item_event,
                details={"worker_termination": True, "termination_event": event},
            )
        traces.record_event(
            context,
            "worker",
            event,
            details={**(details or {}), "closed_work_item_ids": list(closed_items)},
        )
        traces.finish_trace(context, outcome.value, error=error)
    return closed_items


def _stall_observations(
    progress: dict[str, object] | None,
    *,
    monitor_started_at: datetime,
) -> tuple[_StallObservation, ...]:
    """Return independently stalled active WorkItems from one worker snapshot.

    The top-level snapshot is only a compatibility projection of the latest
    event. Parallel nodes live under ``work_items`` and must be monitored
    independently, otherwise a fast sibling reaching terminal state can hide
    a stalled LLM or tool call.
    """
    if not progress:
        return ()
    work_items = progress.get("work_items")
    views = [value for value in work_items.values() if isinstance(value, dict)] if isinstance(work_items, dict) else []

    observations: list[_StallObservation] = []
    for view in views:
        lifecycle = str(view.get("lifecycle", "running"))
        activity = str(view.get("activity", "worker"))
        event = str(view.get("event_type", ""))
        if lifecycle not in {"running", "retrying"}:
            continue
        try:
            observed = view.get("last_event_at")
            if datetime.fromisoformat(str(observed or "")) < monitor_started_at:
                continue
        except (TypeError, ValueError):
            continue

        llm = view.get("llm")
        llm_state = str(llm.get("state", "idle")) if isinstance(llm, dict) else "idle"
        llm_open = activity == "llm" and llm_state in {"started", "streaming"}
        operation_open = event in {"tool_started", "file_write_started"}
        monitorable = llm_open or (
            activity in {"tool", "artifact", "sandbox", "integration"}
            and operation_open
        )
        if not monitorable:
            continue

        meaningful_idle_seconds = meaningful_idle_for(view)
        transport_idle_seconds = transport_idle_for(view)
        transport_threshold = transport_stall_timeout(activity)
        semantic_threshold = semantic_stall_timeout(activity)
        if (
            llm_open
            and transport_idle_seconds is not None
            and transport_idle_seconds >= transport_threshold
        ):
            observations.append(
                _StallObservation(
                    work_item_id=str(view.get("work_item_id", "worker")),
                    activity=activity,
                    kind="provider_transport_stall",
                    idle_seconds=transport_idle_seconds,
                    threshold=transport_threshold,
                    progress_marker=str((view.get("clocks") or {}).get("transport_at", "")),
                )
            )
        elif (
            meaningful_idle_seconds is not None
            and meaningful_idle_seconds >= semantic_threshold
        ):
            observations.append(
                _StallObservation(
                    work_item_id=str(view.get("work_item_id", "worker")),
                    activity=activity,
                    kind="semantic_stall",
                    idle_seconds=meaningful_idle_seconds,
                    threshold=semantic_threshold,
                    progress_marker=str(view.get("last_meaningful_at", "")),
                )
            )
    return tuple(observations)


def _rebuild_delivery_state(
    container: ProjectOSContainer,
    plan,
    trace_id: str,
    *,
    persist: bool = True,
) -> RunState:
    """从交付计划和事件日志迁移旧 Trace 的 repair checkpoint。

    早期版本只保存了当前 repair DAG，导致 Worker 恢复时无法找到原始
    tests/review 节点。已完成节点的事实来自 Trace 事件，正文按已发布产物
    作为可选摘要恢复；真正的上游正文仍由 ArtifactRef 按需读取。
    """
    terminal_events: dict[str, dict[str, object]] = {}
    for event in container.traces.list_events(trace_id):
        work_item_id = str(event.get("work_item_id", ""))
        if not work_item_id or plan.work_item(work_item_id) is None:
            continue
        if event.get("type") in {
            "work_item_completed",
            "work_item_failed",
            "work_item_needs_replan",
            "work_item_waiting_capability",
        }:
            # events.jsonl is append-only; later events for one WorkItem are
            # the authoritative attempt outcome after a retry or resume.
            terminal_events[work_item_id] = event
    completed_ids = {
        work_item_id
        for work_item_id, event in terminal_events.items()
        if event.get("type") == "work_item_completed"
    }
    # A downstream completion is not reusable when a dependency later failed
    # on a retry.  Remove such descendants transitively so integration/tests/
    # review are re-executed against the newest code instead of being skipped
    # with stale evidence.
    changed = True
    while changed:
        changed = False
        for item in plan.work_items:
            if item.id in completed_ids and any(
                dependency not in completed_ids for dependency in item.dependency_ids
            ):
                completed_ids.remove(item.id)
                changed = True
    artifacts: dict[str, str] = {}
    for item in plan.work_items:
        if item.id not in completed_ids:
            continue
        key = item.output_key
        if key in artifacts:
            continue
        try:
            if not container.artifacts.exists(key):
                continue
            artifacts[key] = container.artifacts.load(key)
        except (FileNotFoundError, ValueError):
            continue
    results: dict[str, NodeResult] = {}
    for item in plan.work_items:
        if item.id not in completed_ids:
            continue
        results[item.id] = NodeResult.completed(
            work_item_id=item.id,
            agent_id=item.agent_id,
            content=artifacts.get(item.output_key, ""),
        )
    state = RunState(plan=plan, node_results=results, artifacts=artifacts)
    if persist:
        container.traces.record_delivery_checkpoint(plan.trace, state.as_checkpoint())
    return state


def _apply_monitor_retry_recovery(
    container: ProjectOSContainer,
    trace_id: str,
    state: RunState,
) -> RunState:
    """Apply each unconsumed batch/run retry decision to a resume state.

    The retry ledger names the root cause and interrupted siblings. Completed
    siblings remain in ``node_results``; only the causal frontier and its
    dependents are invalidated. This keeps a parallel wave from replaying
    successful work after a watchdog terminates one Worker process.
    """
    try:
        records = RetryLedger(container.traces.project_path, trace_id).recovery_records()
    except (OSError, ValueError):
        return state
    if not records:
        return state
    consumed = {
        str((event.get("details") or {}).get("retry_record_created_at", ""))
        for event in container.traces.list_events(trace_id)
        if event.get("type") == "retry_recovery_applied"
    }
    descendants: dict[str, set[str]] = {item.id: set() for item in state.plan.work_items}
    for item in state.plan.work_items:
        for dependency in item.dependency_ids:
            descendants.setdefault(dependency, set()).add(item.id)

    for record in records:
        if record.created_at in consumed:
            continue
        frontier = tuple(
            item_id
            for item_id in (record.root_work_item_id, *record.interrupted_work_item_ids)
            if item_id and state.plan.work_item(item_id) is not None
        )
        if not frontier:
            # The Worker may have died before a WorkItem was announced. The
            # record is still audit history, but there is no trustworthy node
            # to invalidate; ordinary checkpoint scheduling remains correct.
            container.traces.record_event(
                state.plan.trace,
                "control",
                "retry_recovery_applied",
                details={
                    "retry_record_created_at": record.created_at,
                    "affected_work_item_ids": [],
                    "reason": "no_active_work_item",
                },
            )
            continue
        invalidated = set(frontier)
        pending = list(frontier)
        while pending:
            current = pending.pop()
            for child in descendants.get(current, ()):
                if child not in invalidated:
                    invalidated.add(child)
                    pending.append(child)
        for item_id in invalidated:
            state.node_results.pop(item_id, None)
            item = state.plan.work_item(item_id)
            if item is not None:
                state.artifacts.pop(item.output_key, None)
        context = {
            "scope": record.scope.value,
            "action": record.action.value,
            "failure": record.failure.as_dict(),
            "root_work_item_id": record.root_work_item_id,
            "interrupted_work_item_ids": list(record.interrupted_work_item_ids),
        }
        for item_id in frontier:
            state.retry_recovery_contexts[item_id] = context
        container.traces.record_event(
            state.plan.trace,
            "control",
            "retry_recovery_applied",
            details={
                "retry_record_created_at": record.created_at,
                "action": record.action.value,
                "root_work_item_id": record.root_work_item_id,
                "interrupted_work_item_ids": list(record.interrupted_work_item_ids),
                "invalidated_work_item_ids": sorted(invalidated),
            },
        )
    return state


def _load_delivery_resume(
    container: ProjectOSContainer,
    trace_id: str,
) -> tuple[object, RunState]:
    """Load the original delivery DAG instead of an active repair DAG."""
    latest = _refresh_repair_plan(container.traces.load_plan(trace_id), container.traces)
    if "-repair-" in latest.id and _repair_execution_pending(container.traces, trace_id):
        try:
            state = RunState.from_checkpoint(
                latest, container.traces.load_checkpoint(trace_id)
            )
        except (FileNotFoundError, ValueError):
            state = RunState(plan=latest)
        # A repair Worker can be interrupted while its repair DAG is active.
        # This branch must consume the same durable monitor decisions as the
        # normal delivery branch before returning a resumable state.
        state = _apply_monitor_retry_recovery(container, trace_id, state)
        state = _sanitize_resume_state(container, state)
        return latest, state
    try:
        plan = _refresh_repair_plan(container.traces.load_delivery_plan(trace_id), container.traces)
    except (FileNotFoundError, ValueError):
        plan = _refresh_repair_plan(container.traces.load_plan(trace_id), container.traces)
    container.traces.validate_plan_baseline(plan)
    # ``delivery-checkpoint.json`` is a pre-repair baseline and may contain an
    # empty state by design. For a normal delivery run, the latest
    # ``checkpoint.json`` is authoritative because GraphRunner updates it
    # after every completed WorkItem. Prefer it to avoid replaying completed
    # nodes after a Provider stall.
    checkpoint_loaders = (
        (container.traces.load_checkpoint, container.traces.load_delivery_checkpoint)
        if "-repair-" not in plan.id
        else (container.traces.load_delivery_checkpoint, container.traces.load_checkpoint)
    )
    state = None
    for load_checkpoint in checkpoint_loaders:
        try:
            checkpoint = load_checkpoint(trace_id)
            if checkpoint.get("plan_id") in {None, plan.id}:
                state = RunState.from_checkpoint(plan, checkpoint)
                # A hard Worker stop can leave a syntactically valid but empty
                # checkpoint. The event log is append-only and already records
                # completed WorkItems, so rebuild from those facts instead of
                # replaying the whole delivery DAG.
                rebuilt = _rebuild_delivery_state(container, plan, trace_id, persist=False)
                # A partial checkpoint can be newer than the original
                # delivery checkpoint but still omit nodes completed before a
                # Worker interruption.  Prefer the event-derived state when
                # it contains additional *latest-successful* WorkItems; this
                # keeps failed nodes pending while preserving prior progress.
                if (
                    not state.forced_rerun_work_item_ids
                    and len(rebuilt.node_results) > len(state.node_results)
                ):
                    state = rebuilt
                break
        except (FileNotFoundError, ValueError):
            continue
    if state is None:
        state = _rebuild_delivery_state(container, plan, trace_id)
    state = _apply_monitor_retry_recovery(container, trace_id, state)
    state = _sanitize_resume_state(container, state)
    return plan, _prepare_architecture_contract_recovery(
        container, trace_id, plan, state
    )


def _sanitize_resume_state(container: ProjectOSContainer, state: RunState) -> RunState:
    """Revalidate persisted architecture hand-offs before resuming downstream work."""
    valid: dict[str, NodeResult] = {}
    for item in state.plan.work_items:
        result = state.node_results.get(item.id)
        if result is None:
            continue
        if any(dep not in valid for dep in item.dependency_ids):
            continue
        if item.agent_id == "architecture_agent" and item.execution_mode.value == "partitioned":
            ref = ArtifactRef.staged(
                artifact_key="architecture",
                trace_id=state.plan.trace.trace_id,
                work_item_id=item.id,
                slot=item.slot or "",
            )
            try:
                container.artifact_repository.load_ref(ref)
            except (FileNotFoundError, RuntimeError, ValueError):
                continue
        # Compiled ids carry a numeric ``wi-XX-`` prefix; use the semantic
        # integration role rather than relying on one historical index.
        if (
            item.agent_id == "architecture_agent"
            and item.execution_mode.value == "integration"
            and item.publish_target == "architecture"
            and item.id.endswith("architecture-layered-integration")
        ):
            try:
                container.artifact_repository.candidate_for_work_item(
                    trace_id=state.plan.trace.trace_id,
                    artifact_key="architecture",
                    work_item_id=item.id,
                )
            except (FileNotFoundError, RuntimeError, ValueError):
                continue
        valid[item.id] = result
    artifacts: dict[str, str] = {}
    for item_id, result in valid.items():
        if result.content is not None:
            artifacts[item_id] = result.content
    state.node_results = valid
    state.artifacts = {
        item.output_key: content
        for item in state.plan.work_items
        for content in [artifacts.get(item.id)]
        if content is not None
    }
    return state


def _architecture_implementation_owners_for_unit(
    container: ProjectOSContainer,
    plan,
    state: RunState,
    unit_id: str,
) -> list:
    """Return completed implementation work items that define one unit id.

    A WorkItem can produce several implementation units, so its slot alone is
    not sufficient proof of ownership. Resolve the unit from its staged
    object. Normally this is a fully validated DTO. For the specific recovery
    path that exists to repair historical validation failures, retain a
    narrow raw-JSON fallback: it only reads the unit id after the repository
    has resolved the manifest-protected staged reference. If evidence is
    missing or ambiguous, recovery is a deliberate no-op.
    """
    owners = []
    for item in plan.work_items:
        result = state.node_results.get(item.id)
        if (
            item.agent_id != "architecture_agent"
            or item.execution_mode is not ExecutionMode.PARTITIONED
            or not (item.slot or "").startswith("implementation-")
            or result is None
            or result.status is not NodeStatus.COMPLETED
        ):
            continue
        ref = ArtifactRef.staged(
            artifact_key="architecture",
            trace_id=plan.trace.trace_id,
            work_item_id=item.id,
            slot=item.slot or "",
        )
        try:
            content = container.artifact_repository.load_ref(ref)
            design = parse_design(content)
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            try:
                raw = json.loads(content)
            except (UnboundLocalError, TypeError, json.JSONDecodeError):
                continue
            units = raw.get("implementation_units") if isinstance(raw, dict) else None
            if (
                raw.get("depth") == 2
                and isinstance(units, list)
                and any(
                    isinstance(unit, dict) and unit.get("unit_id") == unit_id
                    for unit in units
                )
            ):
                owners.append(item)
            continue
        if isinstance(design, ImplementationDesign) and any(
            unit.unit_id == unit_id for unit in design.implementation_units
        ):
            owners.append(item)
    return owners


def _architecture_module_design_owners_for_design_id(
    container: ProjectOSContainer,
    plan,
    state: RunState,
    design_id: str,
) -> list:
    """Return completed depth=1 owners whose staged design has ``design_id``."""
    owners = []
    for item in plan.work_items:
        result = state.node_results.get(item.id)
        if (
            item.agent_id != "architecture_agent"
            or item.execution_mode is not ExecutionMode.PARTITIONED
            or item.stage_id != "architecture_module"
            or result is None
            or result.status is not NodeStatus.COMPLETED
        ):
            continue
        ref = ArtifactRef.staged(
            # Partitioned architecture hand-offs are stored in the shared
            # ``architecture`` artifact namespace.  WorkItem.artifact_key
            # defaults to output_key when omitted, which is an orchestration
            # label (for example ``architecture_module_api``), not a valid
            # ArtifactStore key.
            artifact_key="architecture",
            trace_id=plan.trace.trace_id,
            work_item_id=item.id,
            slot=item.slot or "",
        )
        try:
            design = parse_design(container.artifact_repository.load_ref(ref))
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            continue
        if isinstance(design, ModuleDesign) and design.design_id == design_id:
            owners.append(item)
    return owners


def _architecture_implementation_design_owners_for_design_id(
    container: ProjectOSContainer,
    plan,
    state: RunState,
    design_id: str,
) -> list:
    """Return completed depth=2 owners whose staged design has ``design_id``."""
    owners = []
    for item in plan.work_items:
        result = state.node_results.get(item.id)
        if (
            item.agent_id != "architecture_agent"
            or item.execution_mode is not ExecutionMode.PARTITIONED
            or not (item.slot or "").startswith("implementation-")
            or result is None
            or result.status is not NodeStatus.COMPLETED
        ):
            continue
        ref = ArtifactRef.staged(
            artifact_key="architecture",
            trace_id=plan.trace.trace_id,
            work_item_id=item.id,
            slot=item.slot or "",
        )
        try:
            design = parse_design(container.artifact_repository.load_ref(ref))
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            continue
        if isinstance(design, ImplementationDesign) and design.design_id == design_id:
            owners.append(item)
    return owners


def _architecture_current_module_design_id(
    container: ProjectOSContainer,
    trace_id: str,
    plan,
    state: RunState,
    implementation_item,
) -> str | None:
    """Return the one verified ModuleDesign parent for an implementation item.

    A generic instruction to read the current module design proved too weak
    after a module rerun changed its ``design_id``.  Recovery may name a parent
    ID only when the implementation WorkItem has exactly one completed module
    dependency whose manifest-protected staged artifact parses as the module
    declared by its delivery contract.  Ambiguity intentionally yields no
    value rather than guessing an ID from an old output or prompt.
    """
    contract = implementation_item.delivery_contract or {}
    architecture = contract.get("architecture")
    module_id = architecture.get("module_id") if isinstance(architecture, dict) else None
    if not isinstance(module_id, str) or not module_id:
        return None

    dependency_ids = set(implementation_item.dependency_ids)
    candidates = []
    for item in plan.work_items:
        result = state.node_results.get(item.id)
        item_contract = item.delivery_contract or {}
        item_architecture = item_contract.get("architecture")
        declared_module_id = (
            item_architecture.get("module_id")
            if isinstance(item_architecture, dict)
            else None
        )
        if (
            item.id not in dependency_ids
            or item.agent_id != "architecture_agent"
            or item.execution_mode is not ExecutionMode.PARTITIONED
            or item.stage_id != "architecture_module"
            or declared_module_id != module_id
            or result is None
            or result.status is not NodeStatus.COMPLETED
        ):
            continue
        ref = ArtifactRef.staged(
            artifact_key="architecture",
            trace_id=trace_id,
            work_item_id=item.id,
            slot=item.slot or "",
        )
        try:
            design = parse_design(container.artifact_repository.load_ref(ref))
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            continue
        if isinstance(design, ModuleDesign) and design.module_id == module_id:
            candidates.append(design.design_id)
    return candidates[0] if len(candidates) == 1 else None


def _architecture_dependency_interface_catalogs(
    container: ProjectOSContainer,
    trace_id: str,
    plan,
    state: RunState,
    implementation_item,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Load formal provider interfaces declared by an implementation's dependencies.

    Recovery diagnostics are advisory, so this helper accepts only evidence that
    is both unambiguous and manifest-protected: the implementation WorkItem must
    declare a dependent module in its delivery contract, exactly one completed
    ModuleDesign WorkItem must represent that module, and its staged output must
    parse as a ModuleDesign.  Missing evidence deliberately produces no catalog
    rather than guessing a synonym or reading an untrusted file.
    """
    contract = implementation_item.delivery_contract or {}
    architecture = contract.get("architecture")
    if not isinstance(architecture, dict):
        return ()
    dependencies = architecture.get("depends_on_modules")
    if not isinstance(dependencies, list) or not all(
        isinstance(module_id, str) and module_id for module_id in dependencies
    ):
        return ()

    catalogs: list[tuple[str, tuple[str, ...]]] = []
    for module_id in dependencies:
        candidates = []
        for item in plan.work_items:
            result = state.node_results.get(item.id)
            if (
                item.agent_id != "architecture_agent"
                or item.execution_mode is not ExecutionMode.PARTITIONED
                or result is None
                or result.status is not NodeStatus.COMPLETED
            ):
                continue
            item_contract = item.delivery_contract or {}
            item_architecture = item_contract.get("architecture")
            declared_module_id = (
                item_architecture.get("module_id")
                if isinstance(item_architecture, dict)
                else None
            )
            if (
                item.stage_id == "architecture_module"
                and declared_module_id == module_id
            ):
                candidates.append(item)
        if len(candidates) != 1:
            continue
        module_item = candidates[0]
        ref = ArtifactRef.staged(
            artifact_key="architecture",
            trace_id=trace_id,
            work_item_id=module_item.id,
            slot=module_item.slot or "",
        )
        try:
            design = parse_design(container.artifact_repository.load_ref(ref))
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            continue
        if not isinstance(design, ModuleDesign) or design.module_id != module_id:
            continue
        interface_ids = tuple(
            sorted({interface.interface_id for interface in design.provided_interfaces})
        )
        catalogs.append((module_id, interface_ids))
    return tuple(catalogs)


def _undeclared_interface_recovery_diagnostic(
    *,
    invalid_interface_id: str,
    catalogs: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    """Explain a rejected interface reference without inventing an alias."""
    diagnostic = (
        f"集成校验发现接口 '{invalid_interface_id}' 不在已声明的正式 provider catalog 中。"
        "consumed_interfaces / consumes_interfaces 只能引用依赖模块 ModuleDesign 的"
        "正式 provided_interfaces；请按实际依赖选择现有接口，或先在所属模块显式提供新的正式接口。"
        "不要猜测接口同义词，也不要将 implementation unit ID 写入接口引用字段。"
    )
    if not catalogs:
        return diagnostic
    catalog_text = "; ".join(
        f"{module_id}: {', '.join(interface_ids) if interface_ids else '（无）'}"
        for module_id, interface_ids in catalogs
    )
    return f"{diagnostic} 当前依赖模块的正式接口目录：{catalog_text}。"


def _architecture_blueprint_module_purpose(
    container: ProjectOSContainer,
    trace_id: str,
    plan,
    state: RunState,
    module_id: str,
) -> str | None:
    """Return one manifest-protected Blueprint purpose for ``module_id``.

    Recovery diagnostics must not derive business intent from prompt text or a
    loose Markdown artifact.  Only one completed, staged ArchitectureBlueprint
    whose repository manifest verifies is safe enough to guide a rerun.
    """
    purposes: list[str] = []
    for item in plan.work_items:
        result = state.node_results.get(item.id)
        if (
            item.agent_id != "architecture_agent"
            or item.execution_mode is not ExecutionMode.PARTITIONED
            or item.stage_id != "architecture_blueprint"
            or result is None
            or result.status is not NodeStatus.COMPLETED
        ):
            continue
        ref = ArtifactRef.staged(
            artifact_key=item.artifact_key or "architecture",
            trace_id=trace_id,
            work_item_id=item.id,
            slot=item.slot or "blueprint",
        )
        try:
            design = parse_design(container.artifact_repository.load_ref(ref))
        except (FileNotFoundError, RuntimeError, TypeError, ValueError):
            continue
        if not isinstance(design, ArchitectureBlueprint):
            continue
        matches = [module for module in design.modules if module.module_id == module_id]
        if len(matches) == 1 and matches[0].purpose:
            purposes.append(matches[0].purpose)
    return purposes[0] if len(purposes) == 1 else None


def _prepare_architecture_contract_recovery(
    container: ProjectOSContainer,
    trace_id: str,
    plan,
    state: RunState,
) -> RunState:
    """Schedule one safe rerun for an unambiguously invalid architecture edge.

    The persisted plan and every WorkItem contract remain unchanged.  This is
    deliberately narrower than general replanning: it only invalidates the
    partitioned implementation design named by the integration validator and
    that design's descendants, then makes the owner execute without accepting
    its old staged artifact as a durable recovery candidate.
    """
    latest_failure = next(
        (
            event
            for event in reversed(container.traces.list_events(trace_id))
            if event.get("type") == "work_item_failed"
        ),
        None,
    )
    if latest_failure is None:
        return state
    failed_item = plan.work_item(str(latest_failure.get("work_item_id", "")))
    if (
        failed_item is None
        or failed_item.agent_id != "architecture_agent"
        or failed_item.execution_mode is not ExecutionMode.INTEGRATION
        or failed_item.publish_target != "architecture"
    ):
        return state
    details = latest_failure.get("details", {})
    error = str(details.get("error", "")) if isinstance(details, dict) else ""
    if "architecture_contract_missing" not in error:
        return state
    undeclared_interface = _ARCHITECTURE_UNDECLARED_INTERFACE.search(error)
    required_path_not_owned = _ARCHITECTURE_REQUIRED_PATH_NOT_OWNED.search(error)
    required_file_not_owned = _ARCHITECTURE_REQUIRED_FILE_NOT_OWNED.search(error)
    module_parent_mismatch = _ARCHITECTURE_MODULE_PARENT_MISMATCH.search(error)
    implementation_parent_mismatch = _ARCHITECTURE_IMPLEMENTATION_PARENT_MISMATCH.search(error)
    unknown_implementation_dependency = _ARCHITECTURE_UNKNOWN_IMPLEMENTATION_DEPENDENCY.search(error)
    module_purpose_mismatch = _ARCHITECTURE_MODULE_PURPOSE_MISMATCH.search(error)
    if required_file_not_owned is not None:
        required_file = required_file_not_owned.group(1)
        owners = [
            item
            for item in plan.work_items
            if item.agent_id == "architecture_agent"
            and item.execution_mode is ExecutionMode.PARTITIONED
            and item.stage_id == "architecture_blueprint"
        ]
        diagnostic = (
            "集成校验发现 Blueprint.required_files 中的文件 "
            f"'{required_file}' 没有任何 implementation unit 在 owned_files 中声明。"
            "请重新生成 Blueprint：required_files 只能列出由 implementation units 实际拥有、"
            "并会在交付中产出的具体文件；不要凭空添加入口或包初始化文件。"
        )
    elif module_parent_mismatch is not None:
        design_id = module_parent_mismatch.group(1)
        owners = _architecture_module_design_owners_for_design_id(
            container, plan, state, design_id
        )
        diagnostic = (
            f"集成校验发现模块设计 {design_id} 未引用当前总体蓝图。"
            "请读取当前已完成的 Blueprint，并将 parent_design_id 逐字设置为其 design_id；"
            "不要复用旧运行或旧 Blueprint 的 parent_design_id。"
        )
    elif implementation_parent_mismatch is not None:
        design_id = implementation_parent_mismatch.group(1)
        owners = _architecture_implementation_design_owners_for_design_id(
            container, plan, state, design_id
        )
        expected_parent_design_id = (
            _architecture_current_module_design_id(
                container, trace_id, plan, state, owners[0]
            )
            if len(owners) == 1
            else None
        )
        diagnostic = (
            f"集成校验发现实现设计 {design_id} 未引用已存在的模块设计。"
            "请读取当前已完成的 ModuleDesign，并将 parent_design_id 逐字设置为其 design_id；"
            "不要复用旧运行或旧模块设计的 parent_design_id。"
        )
        if expected_parent_design_id is not None:
            diagnostic += (
                " 已由控制面按该 WorkItem 的唯一已完成模块依赖验证："
                f"本轮 parent_design_id 必须精确为 `{expected_parent_design_id}`。"
            )
    elif unknown_implementation_dependency is not None:
        unit_id, unknown_dependencies = unknown_implementation_dependency.groups()
        owners = _architecture_implementation_owners_for_unit(
            container, plan, state, unit_id
        )
        catalogs = (
            _architecture_dependency_interface_catalogs(
                container, trace_id, plan, state, owners[0]
            )
            if len(owners) == 1
            else ()
        )
        diagnostic = (
            f"集成校验发现 implementation unit '{unit_id}' 的 depends_on 引用了不存在的 "
            f"implementation unit：{unknown_dependencies.strip()}。"
            "depends_on 只能引用本次集成架构中实际存在的 unit_id，且只能依赖更早 wave 的单元；"
            "跨模块业务能力不得写入 depends_on，应在 ImplementationDesign 顶层 "
            "consumed_interfaces 中引用依赖模块已声明的正式 interface_id。"
            "请删除不存在的 unit ID；不要把 module_id 或 interface_id 写入 depends_on。"
        )
        if catalogs:
            catalog_text = "; ".join(
                f"{module_id}: {', '.join(interface_ids) if interface_ids else '（无）'}"
                for module_id, interface_ids in catalogs
            )
            diagnostic += f" 当前依赖模块的正式接口目录：{catalog_text}。"
    elif undeclared_interface is not None:
        design_name, invalid_interface_id = undeclared_interface.groups()
        owners = [
            item
            for item in plan.work_items
            if item.agent_id == "architecture_agent"
            and item.execution_mode is ExecutionMode.PARTITIONED
            and (item.slot or "").startswith("implementation-")
            and (item.slot or "").rsplit("-", 1)[-1] == design_name
        ]
        diagnostic = _undeclared_interface_recovery_diagnostic(
            invalid_interface_id=invalid_interface_id,
            catalogs=(
                _architecture_dependency_interface_catalogs(
                    container, trace_id, plan, state, owners[0]
                )
                if len(owners) == 1
                else ()
            ),
        )
    elif required_path_not_owned is not None:
        unit_id, required_path = required_path_not_owned.groups()
        owners = _architecture_implementation_owners_for_unit(
            container, plan, state, unit_id
        )
        diagnostic = (
            f"集成校验发现 implementation unit '{unit_id}' 将 '{required_path}' 声明为 "
            "required_paths，但该字段只能列出本单元唯一拥有的输出文件。"
            "若该文件由另一个 implementation unit 产出，请通过 depends_on、"
            "required_symbols 或正式接口表达依赖；不要把其他单元的文件写入 required_paths。"
        )
    elif module_purpose_mismatch is not None:
        module_id = module_purpose_mismatch.group(1)
        owners = [
            item
            for item in plan.work_items
            if item.agent_id == "architecture_agent"
            and item.execution_mode is ExecutionMode.PARTITIONED
            and item.stage_id == "architecture_module"
            and isinstance(item.delivery_contract, dict)
            and isinstance(item.delivery_contract.get("architecture"), dict)
            and item.delivery_contract["architecture"].get("depth") == 1
            and item.delivery_contract["architecture"].get("module_id") == module_id
        ]
        purpose = _architecture_blueprint_module_purpose(
            container, trace_id, plan, state, module_id
        )
        if purpose is None:
            return state
        diagnostic = (
            f"集成校验发现模块设计 {module_id} 的 purpose 必须与 Blueprint 完全一致。"
            f"Blueprint 为 {module_id} 声明的正式 purpose：\n“{purpose}”\n"
            "请逐字复用该 purpose；不要在 ModuleDesign 中补充、改写或细化 purpose。"
        )
    else:
        return state
    if len(owners) != 1:
        return state
    owner = owners[0]
    if owner.id in state.forced_rerun_work_item_ids:
        # The checkpoint already represents this scheduled recovery.  Keeping
        # it intact is vital in the isolated Worker, where event rebuilding
        # would otherwise resurrect the pre-recovery durable output.
        return state
    owner_result = state.node_results.get(owner.id)
    if owner_result is None or owner_result.status is not NodeStatus.COMPLETED:
        return state

    descendants: dict[str, set[str]] = {item.id: set() for item in plan.work_items}
    for item in plan.work_items:
        for dependency in item.dependency_ids:
            descendants[dependency].add(item.id)
    invalidated = {owner.id}
    pending = [owner.id]
    while pending:
        current = pending.pop()
        for child in descendants[current]:
            if child not in invalidated:
                invalidated.add(child)
                pending.append(child)
    for item_id in invalidated:
        state.node_results.pop(item_id, None)
        item = plan.work_item(item_id)
        if item is not None:
            state.artifacts.pop(item.output_key, None)

    state.forced_rerun_work_item_ids.add(owner.id)
    state.recovery_diagnostics[owner.id] = diagnostic
    container.traces.record_event(
        plan.trace,
        "control",
        "architecture_contract_recovery_scheduled",
        details={
            "failed_work_item_id": failed_item.id,
            "rerun_work_item_id": owner.id,
            "invalidated_work_item_ids": sorted(invalidated),
            "contract_digests": {
                item_id: plan.work_item(item_id).contract_digest
                for item_id in sorted(invalidated)
                if plan.work_item(item_id) is not None
            },
            "diagnostic": diagnostic,
        },
    )
    return state


def _repair_execution_pending(traces: TraceStore, trace_id: str) -> bool:
    """Return true while the latest repair plan has not reached its result."""
    # A Worker may be interrupted after a repair cycle completed but before
    # ``_run_with_repairs`` restored the original delivery DAG.  In that case
    # the latest plan is still ``*-repair-*`` and the event log's last repair
    # status is historical ``needs_replan``; the authoritative checkpoint is
    # stronger evidence.  Treat a fully completed repair checkpoint as ready
    # for delivery resume so ``tests -> review`` cannot be skipped.
    try:
        latest_plan = traces.load_plan(trace_id)
        if "-repair-" in latest_plan.id:
            checkpoint = traces.load_checkpoint(trace_id)
            state = RunState.from_checkpoint(latest_plan, checkpoint)
            if state.is_complete():
                return False
    except (FileNotFoundError, ValueError, TypeError):
        pass
    last_started = -1
    last_completed = -1
    last_status = ""
    for index, event in enumerate(traces.list_events(trace_id)):
        event_type = event.get("type")
        if event_type == "repair_cycle_started":
            last_started = index
        elif event_type == "repair_cycle_completed":
            last_completed = index
            details = event.get("details") or {}
            last_status = str(details.get("status", ""))
    return last_started > last_completed or (
        last_completed >= last_started and last_status != GraphRunStatus.COMPLETED.value
    )


def _delivery_resume_pending(container: ProjectOSContainer, trace_id: str) -> bool:
    """Whether a terminal Trace still has unfinished original delivery work."""
    try:
        base_plan = container.traces.load_delivery_plan(trace_id)
        latest_plan = container.traces.load_plan(trace_id)
    except (FileNotFoundError, ValueError):
        return False
    if latest_plan.id == base_plan.id and "-repair-" not in latest_plan.id:
        try:
            state = RunState.from_checkpoint(
                base_plan, container.traces.load_checkpoint(trace_id)
            )
            return not state.is_complete()
        except (FileNotFoundError, ValueError):
            return False
    try:
        state = RunState.from_checkpoint(
            base_plan, container.traces.load_delivery_checkpoint(trace_id)
        )
        return not state.is_complete()
    except (FileNotFoundError, ValueError):
        # Migration path for historical traces whose latest plan is a repair
        # plan but which predate delivery-checkpoint.json.
        return True


def _worker_entry(
    project_path: str,
    trace_id: str,
    retry_policy: RetryPolicy | None = None,
) -> None:
    """在隔离进程中重建容器并执行一条 Trace。

    不把 Container、Agent 或 CrewAI 对象跨进程传递；子进程只接收项目目录和
    Trace ID，并从持久化的 plan/checkpoint 恢复，避免第三方全局线程池污染 API 进程。
    """
    traces = None
    progress_store: WorkerProgressStore | None = None
    claimed = False
    try:
        trace_store = TraceStore(project_path)
        # ``spawn`` workers do not inherit the API process' logging handlers.
        # Point the Responses adapter at a per-Trace JSONL sink before the
        # container is rebuilt so SSE transport/event diagnostics survive the
        # process boundary.
        os.environ["PROJECTOS_SSE_DIAGNOSTICS_PATH"] = str(
            trace_store.trace_root(trace_id) / "sse-diagnostics.jsonl"
        )
        selection = trace_store.load_llm_selection(trace_id)
        container = build_container(
            project_path,
            llm_selection=selection,
            llm_overrides=trace_store.load_llm_overrides(trace_id),
            retry_policy=retry_policy,
        )
        traces = container.traces
        _restore_approved_sources(container, trace_id)
        plan, state = _load_delivery_resume(container, trace_id)
        traces.record_event(plan.trace, "worker", "worker_started", details={"pid": os.getpid()})
        progress_store = WorkerProgressStore(project_path)
        if not progress_store.claim_worker(trace_id):
            # Another process already owns this Trace.  This is a normal
            # duplicate-resume race, not a failed delivery.
            raise SystemExit(75)
        claimed = True
        if progress_store.cancel_requested(trace_id):
            trace = traces.load_trace(trace_id)
            from app.orchestration.trace import TraceContext
            context = TraceContext(
                requirement_id=str(trace["requirement_id"]),
                trace_id=trace_id,
                parent_trace_id=trace.get("parent_trace_id"),
            )
            traces.record_event(context, "worker", "worker_cancelled", details={"reason": "cancel_requested_before_start"})
            traces.finish_trace(context, "cancelled", error="运行在 Worker 启动前已取消")
            progress_store.start_run(trace_id, event_type="run_cancelled", worker_process_state="terminated")
            progress_store.finalize_run(
                trace_id,
                outcome=ExecutionOutcome.CANCELLED,
                event="run_cancelled",
                summary="运行已取消",
                details={"termination_reason": "cancel_requested_before_start"},
            )
            progress_store.release_worker(trace_id)
            claimed = False
            return
        progress_store.clear_control(trace_id)
        progress_store.start_run(trace_id, event_type="worker_started", worker_process_state="running")
        progress_store.release_worker(trace_id)
        claimed = False
        heartbeat = WorkerHeartbeat(project_path, trace_id)
        heartbeat.start()
        try:
            result = RunCoordinator._run_with_repairs(container, plan, state)
        finally:
            heartbeat.stop()
        traces.record_event(
            plan.trace,
            "worker",
            "worker_completed",
            details={"pid": os.getpid(), "status": result.status.value},
        )
        run_lifecycle = (
            "waiting"
            if result.status is GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL
            else "retrying"
            if result.status is GraphRunStatus.NEEDS_REPLAN
            else "terminal"
        )
        run_outcome = (
            result.status.value
            if result.status in {
                GraphRunStatus.COMPLETED,
                GraphRunStatus.BLOCKED,
                GraphRunStatus.FAILED,
                GraphRunStatus.CANCELLED,
            }
            else None
        )
        if run_outcome is not None:
            progress_store.finalize_run(
                trace_id,
                outcome=ExecutionOutcome(run_outcome),
                event="worker_completed",
                summary="Worker 已完成运行",
                details={"status": result.status.value},
            )
        else:
            progress_store.set_run_state(
                trace_id,
                lifecycle=ExecutionLifecycle.WAITING if run_lifecycle == "waiting" else ExecutionLifecycle.RETRYING,
                event_type="worker_completed",
                activity=ExecutionActivity.WORKER,
                summary=result.status.value,
            )
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 75:
            # Duplicate Worker lease claimant; the owning process remains the
            # sole writer of Trace terminal state.
            return
        # An API cancellation may terminate the child while it is inside a
        # provider call.  The cancellation request is durable and wins over
        # the generic bootstrap/worker failure fallback.
        if progress_store is not None and progress_store.cancel_requested(trace_id):
            try:
                _finalize_external_run(
                    project_path,
                    trace_id,
                    outcome=ExecutionOutcome.CANCELLED,
                    event="worker_cancelled",
                    summary="Worker 收到取消请求，已完成控制面收口",
                    error="运行已取消",
                    details={
                        "reason": "cancel_requested_during_worker",
                        "source": "worker_exception",
                    },
                )
            except Exception:
                pass
            if claimed:
                progress_store.release_worker(trace_id)
            return
        # GraphRunner normally persists terminal states itself.  This fallback
        # covers failures during container/bootstrap/import before GraphRunner starts.
        try:
            _finalize_external_run(
                project_path,
                trace_id,
                outcome=ExecutionOutcome.FAILED,
                event="worker_failed",
                summary="Worker 异常退出，已完成控制面收口",
                error=_format_error(error),
                details={
                    "pid": os.getpid(),
                    "source": "worker_exception",
                    **_error_details(error),
                },
                retry_policy=retry_policy,
            )
        except Exception:
            pass
        if claimed and progress_store is not None:
            progress_store.release_worker(trace_id)
        raise


def _restore_approved_sources(
    container: ProjectOSContainer, trace_id: str
) -> None:
    """从 Trace 事件恢复跨 Worker 的能力审批。

    ToolGateway 的授权是运行时内存状态，而 Worker 会被隔离进程反复重建。
    ``capability_approved`` 事件是持久事实来源；重放它们可让恢复后的 Worker
    继承此前已批准的 MCP/外部来源，而不会再次卡在同一审批点。
    """
    from app.tool_manager.grants import CapabilityGrantStore

    events = container.traces.list_events(trace_id)
    grant_store = CapabilityGrantStore(container.traces.project_path, trace_id)
    grants = grant_store.list()
    revoked = {
        (grant.capability, grant.source_name)
        for grant in grant_store.list(include_inactive=True)
        if grant.status == "revoked"
    }
    for grant in grants:
        try:
            container.gateway.activate_grant(grant)
        except ValueError:
            continue
    for event in events:
        if event.get("type") != "capability_approved":
            continue
        details = event.get("details") or {}
        capability = str(details.get("capability", "")).strip()
        source_name = str(details.get("source_name", "")).strip()
        if not capability or not source_name:
            continue
        if (capability, source_name) in revoked:
            continue
        try:
            # The grant, rather than a domain-wide activation, is the source of truth.
            from app.tool_manager.grants import CapabilityGrant
            scope = str(details.get("scope", "trace"))
            grant = CapabilityGrant(
                grant_id=f"event-{trace_id}-{source_name}",
                trace_id=trace_id,
                work_item_id=str(details.get("work_item_id")) if details.get("work_item_id") else None,
                capability=capability,
                source_name=source_name,
                scope=scope,
                approved_by=str(details.get("approved_by", "api")),
                approved_at=str(event.get("timestamp", "")),
            )
            container.gateway.activate_grant(grant)
        except ValueError:
            # A removed/renamed source should not make an otherwise inspectable
            # Trace impossible to load; the next capability check will report it.
            continue


class RunCoordinator:
    """负责监管隔离 Worker 的后台执行器。

    API 进程只持有 Future 和子进程句柄；CrewAI、Agent 和 GraphRunner 全部在
    Worker 中重建。Trace 文件是跨进程、跨 API 重启的事实来源。
    """

    def __init__(
        self,
        *,
        max_workers: int = 2,
        worker_timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("RunCoordinator.max_workers 至少为 1")
        if worker_timeout_seconds is None:
            worker_timeout_seconds = float(
                os.environ.get("PROJECTOS_WORKER_TIMEOUT_SECONDS", "900")
            )
        if worker_timeout_seconds <= 0:
            raise ValueError("worker_timeout_seconds 必须大于 0")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="projectos-run"
        )
        self._futures: dict[str, Future[str]] = {}
        self._processes: dict[str, multiprocessing.Process] = {}
        self._worker_timeout_seconds = worker_timeout_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._lock = Lock()

    def submit(
        self,
        container: ProjectOSContainer,
        plan,
        *,
        state: RunState | None = None,
    ) -> None:
        with self._lock:
            existing = self._futures.get(plan.trace.trace_id)
            if existing is not None and not existing.done():
                raise PlannerFailure("Trace 仍在当前进程运行，不能重复提交")
            # Patch/resume may mutate RunState without running GraphRunner first.
            # Persist that state before handing control to the Worker process.
            if state is not None:
                container.traces.record_checkpoint(plan.trace, state.as_checkpoint())
            # The isolated Worker reconstructs the plan from disk.  Persist it
            # before submitting the process; relying on GraphRunner to write it
            # would race with the Worker bootstrap and leave no plan.json.
            container.traces.record_plan(plan)
            container.traces.record_delivery_plan(plan)
        future = self._executor.submit(
            self._run_in_worker,
            container.project_path,
            plan.trace.trace_id,
        )
        with self._lock:
            self._futures[plan.trace.trace_id] = future

    def status(self, trace_id: str) -> str | None:
        with self._lock:
            future = self._futures.get(trace_id)
        if future is None:
            return None
        if not future.done():
            return "running"
        if future.cancelled():
            return "cancelled"
        try:
            return str(future.result())
        except Exception:
            return "failed"

    def cancel(self, project_path: str, trace_id: str, *, reason: str = "api_request") -> dict[str, str]:
        """Request cancellation, stop only this Worker, and persist a terminal state."""
        traces = TraceStore(project_path)
        try:
            payload = traces.load_trace(trace_id)
        except (FileNotFoundError, ValueError):
            raise FileNotFoundError(trace_id)
        current_status = str(payload.get("status", ""))
        terminal_statuses = {
            GraphRunStatus.COMPLETED.value,
            GraphRunStatus.BLOCKED.value,
            GraphRunStatus.FAILED.value,
            GraphRunStatus.CANCELLED.value,
        }
        if current_status in terminal_statuses:
            return {"trace_id": trace_id, "status": current_status, "worker_process_state": current_status}
        store = WorkerProgressStore(project_path)
        store.request_cancel(trace_id, reason=reason)
        from app.orchestration.trace import TraceContext
        context = TraceContext(
            requirement_id=str(payload["requirement_id"]),
            trace_id=trace_id,
            parent_trace_id=payload.get("parent_trace_id"),
        )
        traces.record_event(context, "worker", "cancel_requested", details={"reason": reason})
        with self._lock:
            process = self._processes.get(trace_id)
            future = self._futures.get(trace_id)
        if process is not None and process.is_alive():
            self._stop_process(process, join_timeout=2)
        try:
            _finalize_external_run(
                project_path,
                trace_id,
                outcome=ExecutionOutcome.CANCELLED,
                event="worker_cancelled",
                summary="运行已取消并完成控制面收口",
                error="运行已取消",
                details={"reason": reason, "source": "coordinator.cancel"},
            )
        except Exception:
            pass
        with self._lock:
            self._processes.pop(trace_id, None)
        process_state = "cancelled" if future is None or not future.done() else self.status(trace_id) or "cancelled"
        return {"trace_id": trace_id, "status": "cancelled", "worker_process_state": "cancelled" if process_state in {"running", "failed"} else process_state}

    def shutdown(self) -> None:
        with self._lock:
            processes = tuple(self._processes.values())
        for process in processes:
            if process.is_alive():
                self._stop_process(process)
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _stop_process(process: multiprocessing.Process, *, join_timeout: float = 5) -> None:
        """优先优雅终止，超时后强制杀死阻塞的 Worker。"""
        process.terminate()
        process.join(timeout=join_timeout)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if kill is not None:
                kill()
                process.join(timeout=join_timeout)

    def _run_in_worker(self, project_path: str, trace_id: str) -> str:
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_worker_entry,
            args=(project_path, trace_id, self._retry_policy),
            name=f"projectos-worker-{trace_id}",
        )
        with self._lock:
            self._processes[trace_id] = process
        started = datetime.now(timezone.utc)
        process.start()
        progress_store = WorkerProgressStore(project_path)
        stall_windows: dict[str, _StallWindow] = {}
        try:
            provider_stall_grace_seconds = max(
                10.0, float(os.environ.get("PROJECTOS_PROVIDER_STALL_GRACE_SECONDS", "120"))
            )
        except ValueError:
            provider_stall_grace_seconds = 120.0
        monitor_started_at = started
        while process.is_alive():
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            remaining = self._worker_timeout_seconds - elapsed
            if remaining <= 0:
                break
            wait_timeout = min(0.5, remaining)
            # Do not wait on a process sentinel or call join(timeout) here. On
            # macOS either can be delayed by provider/background threads owned
            # by the child. The parent clock must drive monitoring independently.
            time.sleep(min(0.5, wait_timeout))
            if not process.is_alive():
                break
            if progress_store.cancel_requested(trace_id):
                self._stop_process(process)
                try:
                    _finalize_external_run(
                        project_path,
                        trace_id,
                        outcome=ExecutionOutcome.CANCELLED,
                        event="worker_cancelled",
                        summary="监控到取消请求，已完成控制面收口",
                        error="运行已取消",
                        details={"source": "coordinator.monitor"},
                    )
                except Exception:
                    pass
                with self._lock:
                    self._processes.pop(trace_id, None)
                return "cancelled"
            progress = progress_store.read(trace_id)
            heartbeat_seconds = heartbeat_for(progress)
            observations = {
                observation.work_item_id: observation
                for observation in _stall_observations(
                    progress, monitor_started_at=monitor_started_at
                )
            }
            for work_item_id, window in tuple(stall_windows.items()):
                observation = observations.get(work_item_id)
                if observation is None or (
                    observation.kind != window.observation.kind
                    or observation.progress_marker
                    != window.observation.progress_marker
                ):
                    # The WorkItem completed, changed activity, or produced a
                    # signal in the channel that established the suspicion.
                    stall_windows.pop(work_item_id, None)

            for work_item_id, observation in observations.items():
                if work_item_id in stall_windows:
                    continue
                stall_windows[work_item_id] = _StallWindow(
                    observed_at=elapsed,
                    observation=observation,
                )
                progress_store.request_provider_stall(
                    trace_id,
                    reason=(
                        f"{observation.kind}: {observation.activity} 活动的 "
                        f"{work_item_id} 超过 {observation.threshold:g} 秒没有对应进度"
                    ),
                )
                try:
                    trace = TraceStore(project_path).load_trace(trace_id)
                    from app.orchestration.trace import TraceContext

                    context_data = TraceContext(
                        requirement_id=str(trace["requirement_id"]),
                        trace_id=trace_id,
                        parent_trace_id=trace.get("parent_trace_id"),
                    )
                    TraceStore(project_path).record_event(
                        context_data,
                        work_item_id,
                        "worker_idle_suspected",
                        details={
                            "activity": observation.activity,
                            "stall_kind": observation.kind,
                            "idle_seconds": observation.idle_seconds,
                            "idle_timeout_seconds": observation.threshold,
                            "grace_seconds": provider_stall_grace_seconds,
                        },
                    )
                    TraceStore(project_path).record_event(
                        context_data,
                        work_item_id,
                        "provider_stalled",
                        details={
                            "activity": observation.activity,
                            "stall_kind": observation.kind,
                            "idle_seconds": observation.idle_seconds,
                            "heartbeat_seconds": heartbeat_seconds,
                            "action": "provider_stall_recorded_no_worker_cancel",
                        },
                    )
                except Exception:
                    pass

            expired = [
                window
                for window in stall_windows.values()
                if elapsed - window.observed_at >= provider_stall_grace_seconds
            ]
            if expired:
                stalled = min(expired, key=lambda window: window.observed_at).observation
                self._stop_process(process)
                traces = TraceStore(project_path)
                try:
                    trace = traces.load_trace(trace_id)
                    from app.orchestration.trace import TraceContext
                    context_data = TraceContext(
                        requirement_id=str(trace["requirement_id"]), trace_id=trace_id,
                        parent_trace_id=trace.get("parent_trace_id"),
                    )
                    error = (
                        f"WorkItem '{stalled.work_item_id}' 出现 {stalled.kind}，在 "
                        f"{provider_stall_grace_seconds:g} 秒宽限期内未恢复，"
                        "已终止当前 Worker，可从 checkpoint 重试"
                    )
                    _finalize_external_run(
                        project_path,
                        trace_id,
                        outcome=ExecutionOutcome.FAILED,
                        event="provider_stall_timeout",
                        summary="Provider/语义进度超时，已完成控制面收口",
                        error=error,
                        details={
                            "activity": stalled.activity,
                            "stall_kind": stalled.kind,
                            "idle_timeout_seconds": stalled.threshold,
                            "grace_seconds": provider_stall_grace_seconds,
                            "work_item_id": stalled.work_item_id,
                            "contract_digest": ((progress_store.read(trace_id) or {}).get("work_items", {}).get(stalled.work_item_id, {}) or {}).get("contract_digest"),
                        },
                        retry_policy=self._retry_policy,
                    )
                except Exception:
                    pass
                with self._lock:
                    self._processes.pop(trace_id, None)
                return "failed"
        if process.is_alive():
            if progress_store.cancel_requested(trace_id):
                self._stop_process(process)
                try:
                    _finalize_external_run(
                        project_path,
                        trace_id,
                        outcome=ExecutionOutcome.CANCELLED,
                        event="worker_cancelled",
                        summary="Worker 超时前收到取消请求，已完成控制面收口",
                        error="运行已取消",
                        details={"source": "coordinator.timeout_boundary"},
                    )
                except Exception:
                    pass
                with self._lock:
                    self._processes.pop(trace_id, None)
                return "cancelled"
            self._stop_process(process)
            try:
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                _finalize_external_run(
                    project_path,
                    trace_id,
                    outcome=ExecutionOutcome.FAILED,
                    event="worker_timed_out",
                    summary="Worker 超时，已完成控制面收口",
                    error=f"Worker 执行超过 {self._worker_timeout_seconds:g} 秒，已终止",
                    details={
                        "timeout_seconds": self._worker_timeout_seconds,
                        "elapsed_seconds": elapsed,
                        "stalled_work_items": sorted(stall_windows),
                        "work_item_id": next(iter(sorted(stall_windows)), None),
                    },
                    retry_policy=self._retry_policy,
                )
            except Exception:
                pass
            result = "failed"
        else:
            try:
                if process.exitcode == 75:
                    # A concurrent resume lost the persistent Worker lease;
                    # leave the owner's Trace state untouched.
                    result = "duplicate"
                    with self._lock:
                        self._processes.pop(trace_id, None)
                    return result
                payload = TraceStore(project_path).load_trace(trace_id)
                result = str(payload.get("status", "failed"))
                if progress_store.cancel_requested(trace_id) and result in {"planned", "running"}:
                    _finalize_external_run(
                        project_path,
                        trace_id,
                        outcome=ExecutionOutcome.CANCELLED,
                        event="worker_cancelled",
                        summary="Worker 检测到取消请求，已完成控制面收口",
                        error="运行已取消",
                        details={"source": "worker_exit"},
                    )
                    result = "cancelled"
                if result in {"planned", "running"}:
                    _finalize_external_run(
                        project_path,
                        trace_id,
                        outcome=ExecutionOutcome.FAILED,
                        event="worker_failed",
                        summary="Worker 未写入终态即退出，已完成控制面收口",
                        error="Worker 在未写入终态前退出",
                        details={
                            "pid": process.pid,
                            "exitcode": process.exitcode,
                            "source": "coordinator.process_exit",
                        },
                        retry_policy=self._retry_policy,
                    )
                    result = "failed"
            except Exception:
                result = "failed" if process.exitcode else "completed"
        with self._lock:
            self._processes.pop(trace_id, None)
        return result

    @staticmethod
    def _run_with_repairs(
        container: ProjectOSContainer,
        plan,
        state: RunState | None = None,
    ) -> GraphRunResult:
        result = container.runner.run(plan, state=state)
        # GraphRunner may expand project_delivery after the architecture contract
        # completes. Subsequent repair planning and checkpoint restoration must use
        # that expanded DAG, not the pre-expansion template snapshot.
        plan = result.state.plan
        # Keep a durable copy of the real delivery DAG.  A repair plan is an
        # append-only diagnostic workflow and must never become the resume
        # baseline for the original tests/review chain.
        container.traces.record_delivery_plan(plan)
        for repair_attempt in range(1, 3):
            # Worker 恢复可能经过持久化/反序列化，状态字段不应依赖
            # Enum 对象身份；使用值语义保持跨进程一致。
            if result.status != GraphRunStatus.NEEDS_REPLAN:
                break
            failure = result.failure_signal or (
                result.node_result.failure_signal
                if result.node_result is not None
                else None
            )
            if failure is None:
                break
            # Save the original DAG state before the repair Worker replaces
            # plan.json/checkpoint.json with its local repair plan.
            container.traces.record_delivery_checkpoint(
                plan.trace,
                result.state.as_checkpoint(),
            )
            repair_scope = _repair_scope(plan, result.node_result.work_item_id if result.node_result else "")
            container.traces.record_event(
                plan.trace,
                "control",
                "repair_cycle_started",
                details={
                    "attempt": repair_attempt,
                    "failed_work_item_id": result.node_result.work_item_id
                    if result.node_result
                    else None,
                    "failure_kind": failure.kind.value,
                    "repair_scope": list(repair_scope),
                },
            )
            try:
                planning = container.planner.plan_repair(
                    previous_plan=plan,
                    failure=failure,
                    plan_id=f"{plan.id}-repair-{repair_attempt}",
                    repair_scope=repair_scope,
                )
            except BaseException as error:
                container.traces.record_event(
                    plan.trace,
                    "control",
                    "repair_cycle_failed",
                    details={
                        "attempt": repair_attempt,
                        "failed_work_item_id": result.node_result.work_item_id
                        if result.node_result
                        else None,
                        "failure_kind": failure.kind.value,
                        **_error_details(error),
                    },
                )
                raise

            repair_result = container.runner.run(planning.plan)
            container.traces.record_event(
                plan.trace,
                "control",
                "repair_cycle_completed",
                details={
                    "attempt": repair_attempt,
                    "status": getattr(repair_result.status, "value", str(repair_result.status)),
                    "repair_plan_id": planning.plan.id,
                },
            )
            if repair_result.status != GraphRunStatus.COMPLETED:
                result = repair_result
                break
            # 修复成功后从原计划 checkpoint 恢复，执行剩余未完成节点（如 review），
            # 避免交付链在修复后绕过质量门直接完成。
            container.traces.mark_running(plan.trace.trace_id)
            plan, state = _load_delivery_resume(container, plan.trace.trace_id)
            result = container.runner.run(plan, state=state)
            if result.status != GraphRunStatus.NEEDS_REPLAN:
                break
        return result


def _repair_scope(plan, failed_id: str) -> tuple[str, ...]:
    """按依赖图计算失败节点的局部修复窗口，而不是固定重做整条计划。"""
    if not failed_id:
        return ()
    item = plan.work_item(failed_id)
    if item is None:
        return (failed_id,)
    scope = {failed_id, *item.dependency_ids}
    for candidate in plan.work_items:
        if failed_id in candidate.dependency_ids:
            scope.add(candidate.id)
    return tuple(sorted(scope))


def _refresh_repair_plan(plan, traces: TraceStore):
    """补全历史修复计划中的结构化证据和实现写入范围。"""
    refreshed = []
    changed = False
    for item in plan.work_items:
        package = item.failure_package
        if package is None or package.signal.evidence_id is None:
            refreshed.append(item)
            continue
        enriched = traces.failure_package(plan.trace, package.signal)
        enriched = replace(
            enriched,
            repair_scope=package.repair_scope or enriched.repair_scope,
            forbidden_rework=package.forbidden_rework or enriched.forbidden_rework,
            unsatisfied_constraints=package.unsatisfied_constraints or enriched.unsatisfied_constraints,
            satisfied_constraints=package.satisfied_constraints or enriched.satisfied_constraints,
        )
        next_item = item
        if item.agent_id == "code_agent":
            # Test failures can mention both implementation and test files.
            # CodeAgent is never allowed to repair the test suite itself; keep
            # only production workspace paths in its EXCLUSIVE repair window.
            candidate_paths = tuple(
                path
                for path in (enriched.repair_paths or item.allowed_paths or item.required_paths)
                if not _is_test_path(path)
            )
            # When traceback parsing found concrete modules, prefer those over
            # a broad layer glob.  A model writing a plausible-but-unused
            # ``order_service.py`` must not satisfy a failure importing
            # ``app.application.orders.OrderService``.
            exact_paths = tuple(path for path in candidate_paths if "*" not in path)
            paths = exact_paths or candidate_paths
            forbidden = tuple(dict.fromkeys((
                *enriched.forbidden_rework,
                "tests/**", ".projectos/**", "project.yaml", "runtime.yaml",
            )))
            if paths and (item.allowed_paths != tuple(paths) or item.forbidden_paths != forbidden):
                revised_item = replace(
                    item,
                    allowed_paths=tuple(paths),
                    forbidden_paths=forbidden,
                    contract_digest=None,
                )
                item.validate_scope_transition(revised_item)
                next_item = revised_item
                changed = True
        if next_item.failure_package != enriched:
            next_item = replace(next_item, failure_package=enriched)
            changed = True
        refreshed.append(next_item)
    if not changed:
        return plan
    updated = replace(plan, work_items=tuple(refreshed))
    traces.record_plan(updated)
    return updated


def _is_test_path(path: str) -> bool:
    """识别不应授予 CodeAgent 的测试文件/目录路径。"""
    normalized = path.replace("\\", "/").lstrip("/")
    name = normalized.rsplit("/", 1)[-1].lower()
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts"))
    )


class RunService:
    """创建受控 Workflow 的计划并将其提交给后台执行器。"""

    def __init__(
        self,
        *,
        coordinator: RunCoordinator,
        container_builder: ContainerBuilder = build_container,
    ) -> None:
        self._coordinator = coordinator
        self._container_builder = container_builder

    def start_controlled_workflow(
        self, *, project_path: str, goal: str, workflow_id: str,
        llm_selection: LLMSelection | None = None,
        llm_overrides: dict[str, LLMSelection] | None = None,
    ) -> StartedRun:
        container = self._container_builder(
            project_path,
            llm_selection=llm_selection,
            llm_overrides=llm_overrides,
        )
        plan_id = f"run-{uuid4().hex[:12]}"
        try:
            template = container.templates.get(workflow_id)
            if template is None:
                raise PlannerFailure(f"未注册 Workflow: '{workflow_id}'")
            required_artifacts = {
                artifact_key
                for node in template.nodes
                for artifact_key in node.input_refs
            }
            produced_artifacts = {
                node.artifact_key or node.publish_target or node.output_key
                for node in template.nodes
            }
            missing = sorted(
                artifact_key
                for artifact_key in required_artifacts
                if artifact_key not in produced_artifacts
                and not container.artifacts.exists(artifact_key)
            )
            if missing:
                raise PlannerFailure(
                    "Workflow 缺少已发布前置产物: " + ", ".join(missing)
                )
            planning = container.planner.plan_controlled_workflow(
                goal=goal, plan_id=plan_id, workflow_id=workflow_id
            )
        except PlannerFailure:
            raise
        container.traces.record_plan_baseline(planning.plan)
        container.traces.set_llm_selection(planning.plan.trace.trace_id, container.llm_selection)
        container.traces.set_llm_overrides(
            planning.plan.trace.trace_id, container.llm_overrides
        )
        self._coordinator.submit(container, planning.plan)
        return StartedRun(
            trace_id=planning.plan.trace.trace_id,
            plan_id=planning.plan.id,
            workflow_id=workflow_id,
            status="running",
        )

    def start_dynamic_plan(
        self, *, project_path: str, goal: str, plan_id: str,
        llm_selection: LLMSelection | None = None,
        llm_overrides: dict[str, LLMSelection] | None = None,
    ) -> StartedRun:
        """为连续会话执行一次普通 Planner 计划，不接受调用方注入权限字段。"""
        container = self._container_builder(
            project_path,
            llm_selection=llm_selection,
            llm_overrides=llm_overrides,
        )
        planning = container.planner.plan(goal=goal, plan_id=plan_id)
        container.traces.record_plan_baseline(planning.plan)
        container.traces.set_llm_selection(planning.plan.trace.trace_id, container.llm_selection)
        container.traces.set_llm_overrides(
            planning.plan.trace.trace_id, container.llm_overrides
        )
        self._coordinator.submit(container, planning.plan)
        return StartedRun(
            trace_id=planning.plan.trace.trace_id,
            plan_id=planning.plan.id,
            workflow_id=planning.plan.template_id or "dynamic",
            status="running",
        )

    def start_patch_plan(
        self,
        *,
        project_path: str,
        trace_id: str,
        change_request: str,
    ) -> StartedRun:
        """在原 Trace checkpoint 上应用局部 PlanPatch，只重跑受影响子图。"""
        trace_store = TraceStore(project_path)
        persisted_selection = trace_store.load_llm_selection(trace_id)
        container = self._container_builder(
            project_path,
            llm_selection=persisted_selection,
            llm_overrides=trace_store.load_llm_overrides(trace_id),
        )
        trace = container.traces.load_trace(trace_id)
        status = str(trace.get("status", ""))
        if status in {"planned", "running"}:
            raise PlannerFailure("当前 Trace 仍在运行，不能应用局部修改")
        previous_plan = container.traces.load_plan(trace_id)
        try:
            checkpoint = container.traces.load_checkpoint(trace_id)
            state = RunState.from_checkpoint(previous_plan, checkpoint)
        except FileNotFoundError:
            state = RunState(plan=previous_plan)
        completed_ids = set(state.node_results)
        patching = container.planner.plan_patch(
            previous_plan=previous_plan,
            change_request=change_request,
            completed_work_item_ids=completed_ids,
            allow_completed_revision=True,
        )
        old_items = {item.id: item for item in previous_plan.work_items}
        invalidated = set(patching.invalidated_work_item_ids)
        state.node_results = {
            item_id: result
            for item_id, result in state.node_results.items()
            if item_id not in invalidated and patching.plan.work_item(item_id) is not None
        }
        invalidated_output_keys = {
            old_items[item_id].output_key
            for item_id in invalidated
            if item_id in old_items
        }
        state.artifacts = {
            key: value
            for key, value in state.artifacts.items()
            if key not in invalidated_output_keys
        }
        state.plan = patching.plan
        container.traces.record_plan_baseline(patching.plan)
        container.traces.record_plan(patching.plan)
        container.traces.record_delivery_plan(patching.plan, overwrite=True)
        container.traces.record_event(
            patching.plan.trace,
            "control",
            "plan_patch_applied",
            details={
                "base_plan_id": previous_plan.id,
                "new_plan_id": patching.plan.id,
                "invalidated_work_item_ids": sorted(invalidated),
                "added_work_item_ids": list(patching.added_work_item_ids),
                "removed_work_item_ids": list(patching.removed_work_item_ids),
            },
        )
        self._coordinator.submit(container, patching.plan, state=state)
        return StartedRun(
            trace_id=trace_id,
            plan_id=patching.plan.id,
            workflow_id=patching.plan.template_id or "dynamic",
            status="running",
        )

    def controlled_workflows(self, project_path: str) -> tuple[dict[str, str], ...]:
        container = self._container_builder(project_path)
        return tuple(
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
            }
            for template in container.templates.templates()
            if template.has_controlled_execution
        )

    def resume_run(
        self, *, project_path: str, trace_id: str, source_name: str | None = None,
        scope: str = "trace"
    ) -> StartedRun:
        """从最近 checkpoint 恢复；可在同一恢复动作中批准一个动态来源。"""
        trace_store = TraceStore(project_path)
        persisted_selection = trace_store.load_llm_selection(trace_id)
        container = self._container_builder(
            project_path,
            llm_selection=persisted_selection,
            llm_overrides=trace_store.load_llm_overrides(trace_id),
        )
        _restore_approved_sources(container, trace_id)
        trace = container.traces.load_trace(trace_id)
        status = str(trace.get("status", ""))
        resumable = {
            GraphRunStatus.FAILED.value,
            GraphRunStatus.BLOCKED.value,
            GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL.value,
            GraphRunStatus.NEEDS_REPLAN.value,
        }
        if status == GraphRunStatus.COMPLETED.value and _delivery_resume_pending(container, trace_id):
            # A historical repair Worker could mark the Trace completed before
            # the original delivery DAG was resumed.  Treat that state as
            # resumable until tests/review finish.
            resumable.add(status)
        # API 进程重启后，Trace 持久化状态可能仍是 planned/running，但原进程
        # 的 Future 已经不存在。此时允许从 checkpoint 恢复，避免把一条可恢复
        # 的交付链永久遗留为“运行中”。同一进程仍在执行时必须拒绝重复提交。
        if status in {"planned", "running"}:
            if self._coordinator.status(trace_id) is not None:
                raise PlannerFailure("Trace 仍在当前进程运行，不能重复恢复")
            if WorkerProgressStore(project_path).worker_active(trace_id):
                raise PlannerFailure("Trace 已有 Worker 正在运行，不能重复恢复")
            resumable.add(status)
        if status not in resumable:
            raise PlannerFailure(f"Trace 当前状态不可恢复: {status}")
        if status == GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL.value and source_name is None:
            waiting = next(
                (
                    event
                    for event in reversed(container.traces.list_events(trace_id))
                    if event.get("type") == "work_item_waiting_capability"
                ),
                None,
            )
            capability = normalize_capability(
                str((waiting or {}).get("details", {}).get("capability", ""))
            )
            environment_ready = (
                capability == "environment_preparation"
                and EnvironmentProvisioner().status(project_path).get("ok") is True
            )
            if not environment_ready:
                raise PlannerFailure("当前 Trace 等待能力审批，请先调用 capabilities/approve")
        active_plan = _refresh_repair_plan(container.traces.load_plan(trace_id), container.traces)
        if source_name is not None:
            waiting = next(
                (
                    event
                    for event in reversed(container.traces.list_events(trace_id))
                    if event.get("type") == "work_item_waiting_capability"
                ),
                None,
            )
            if waiting is None:
                raise PlannerFailure("当前 Trace 没有待批准的能力请求")
            work_item = active_plan.work_item(str(waiting.get("work_item_id", "")))
            if work_item is None:
                raise PlannerFailure("能力请求对应的 WorkItem 不存在")
            definition = container.agents.definition(work_item.agent_id)
            if definition is None:
                raise PlannerFailure(f"能力请求 Agent 未注册: {work_item.agent_id}")
            capability = str(waiting.get("details", {}).get("capability", ""))
            candidates = container.gateway.find_sources_for_capability(
                definition.domain, capability
            )
            candidate = next(
                (source for source in candidates if source.source_name == source_name),
                None,
            )
            if candidate is None:
                raise PlannerFailure(
                    f"来源 '{source_name}' 不能满足能力 '{capability}'"
                )
            # 授权范围由 scope 决定；Gateway 会在每次工具暴露和执行时校验它。
            if scope not in {"node", "trace", "project"}:
                raise PlannerFailure("能力授权 scope 必须是 node、trace 或 project")
            from app.tool_manager.grants import CapabilityGrantStore
            grant = CapabilityGrantStore(project_path, trace_id).grant(
                capability=capability,
                source_name=source_name,
                work_item_id=work_item.id,
                scope=scope,
                approved_by="api",
            )
            container.gateway.activate_grant(grant)
            container.traces.record_event(
                active_plan.trace,
                "control",
                "capability_approved",
                details={
                    "source_name": source_name,
                    "capability": capability,
                    "work_item_id": work_item.id,
                    "scope": scope,
                    "approved_by": "api",
                    "grant_id": grant.grant_id,
                },
            )
        plan, state = _load_delivery_resume(container, trace_id)
        # Persist any repair-package enrichment before the Worker is started.
        container.traces.record_plan(plan)
        # Persist the new lifecycle state before the worker starts so API
        # readers never observe historical ``failed`` while execution runs.
        container.traces.mark_running(trace_id)
        self._coordinator.submit(container, plan, state=state)
        return StartedRun(
            trace_id=trace_id,
            plan_id=plan.id,
            workflow_id=plan.template_id or "",
            status="running",
        )

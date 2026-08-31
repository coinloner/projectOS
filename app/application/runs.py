"""HTTP、CLI 等入口共用的运行应用服务。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import multiprocessing
import os
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
from app.orchestration.node_result import NodeResult
from app.artifact.repository import ArtifactRef
from app.orchestration.trace import TraceStore
from app.planner.service import PlannerFailure
from app.orchestration.progress import (
    WorkerHeartbeat,
    WorkerProgressStore,
    heartbeat_for,
    idle_for,
    phase_idle_timeout,
)
from app.llm.config import LLMSelection


ContainerBuilder = Callable[[str], ProjectOSContainer]


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


def _rebuild_delivery_state(
    container: ProjectOSContainer,
    plan,
    trace_id: str,
) -> RunState:
    """从交付计划和事件日志迁移旧 Trace 的 repair checkpoint。

    早期版本只保存了当前 repair DAG，导致 Worker 恢复时无法找到原始
    tests/review 节点。已完成节点的事实来自 Trace 事件，正文按已发布产物
    作为可选摘要恢复；真正的上游正文仍由 ArtifactRef 按需读取。
    """
    completed_ids = {
        str(event.get("work_item_id", ""))
        for event in container.traces.list_events(trace_id)
        if event.get("type") == "work_item_completed"
        and plan.work_item(str(event.get("work_item_id", ""))) is not None
    }
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
            node_id=item.id,
            agent_id=item.agent_id,
            content=artifacts.get(item.output_key, ""),
        )
    state = RunState(plan=plan, node_results=results, artifacts=artifacts)
    container.traces.record_delivery_checkpoint(plan.trace, state.as_checkpoint())
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
        return latest, state
    try:
        plan = _refresh_repair_plan(container.traces.load_delivery_plan(trace_id), container.traces)
    except (FileNotFoundError, ValueError):
        plan = _refresh_repair_plan(container.traces.load_plan(trace_id), container.traces)
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
                if not state.node_results:
                    rebuilt = _rebuild_delivery_state(container, plan, trace_id)
                    if rebuilt.node_results:
                        state = rebuilt
                break
        except (FileNotFoundError, ValueError):
            continue
    if state is None:
        state = _rebuild_delivery_state(container, plan, trace_id)
    return plan, _sanitize_resume_state(container, state)


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
                slot=item.output_slot or "",
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


def _repair_execution_pending(traces: TraceStore, trace_id: str) -> bool:
    """Return true while the latest repair plan has not reached its result."""
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


def _worker_entry(project_path: str, trace_id: str) -> None:
    """在隔离进程中重建容器并执行一条 Trace。

    不把 Container、Agent 或 CrewAI 对象跨进程传递；子进程只接收项目目录和
    Trace ID，并从持久化的 plan/checkpoint 恢复，避免第三方全局线程池污染 API 进程。
    """
    traces = None
    progress_store: WorkerProgressStore | None = None
    claimed = False
    try:
        trace_store = TraceStore(project_path)
        selection = trace_store.load_llm_selection(trace_id)
        container = build_container(
            project_path,
            llm_selection=selection,
            llm_overrides=trace_store.load_llm_overrides(trace_id),
        )
        traces = container.traces
        _restore_approved_sources(container, trace_id)
        plan, state = _load_delivery_resume(container, trace_id)
        traces.record_event(plan.trace, "worker", "worker_started", details={"pid": os.getpid()})
        now = datetime.now(timezone.utc).isoformat()
        progress_store = WorkerProgressStore(project_path)
        if not progress_store.claim_worker(trace_id):
            # Another process already owns this Trace.  This is a normal
            # duplicate-resume race, not a failed delivery.
            raise SystemExit(75)
        claimed = True
        progress_store.clear_control(trace_id)
        progress_store.write(
            trace_id,
            {
                "trace_id": trace_id,
                "phase": "running",
                "event": "worker_started",
                "sequence": 0,
                "started_at": now,
                "last_progress_at": now,
                "heartbeat_at": now,
                "counters": {"llm_calls": 0, "llm_chunks": 0, "llm_bytes": 0, "tool_calls": 0},
            },
        )
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
        progress_store = WorkerProgressStore(project_path)
        current = progress_store.read(trace_id) or {}
        now = datetime.now(timezone.utc).isoformat()
        progress_store.write(
            trace_id,
            {
                **current,
                "phase": result.status.value,
                "event": "worker_completed",
                "last_progress_at": now,
                "finished_at": now,
                "details": {"status": result.status.value},
            },
        )
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 75:
            # Duplicate Worker lease claimant; the owning process remains the
            # sole writer of Trace terminal state.
            return
        # GraphRunner normally persists terminal states itself.  This fallback
        # covers failures during container/bootstrap/import before GraphRunner starts.
        if traces is not None:
            try:
                trace = traces.load_trace(trace_id)
                from app.orchestration.trace import TraceContext

                context = TraceContext(
                    requirement_id=str(trace["requirement_id"]),
                    trace_id=trace_id,
                    parent_trace_id=trace.get("parent_trace_id"),
                )
                traces.record_event(
                    context,
                    "worker",
                    "worker_failed",
                    details={"pid": os.getpid(), **_error_details(error)},
                )
                traces.finish_trace(
                    context,
                    "failed",
                    error=_format_error(error),
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
            args=(project_path, trace_id),
            name=f"projectos-worker-{trace_id}",
        )
        with self._lock:
            self._processes[trace_id] = process
        started = datetime.now(timezone.utc)
        process.start()
        progress_store = WorkerProgressStore(project_path)
        idle_suspected_at: float | None = None
        idle_phase: str | None = None
        idle_timeout: float | None = None
        idle_progress_marker: str | None = None
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
            progress = progress_store.read(trace_id)
            phase = str(progress.get("phase", "running")) if progress else "running"
            idle_seconds = idle_for(progress)
            if progress:
                try:
                    progress_timestamp = datetime.fromisoformat(
                        str(progress.get("last_progress_at", ""))
                    )
                    if progress_timestamp < monitor_started_at:
                        # A resumed Trace may contain a stale status snapshot
                        # from the previous Worker.  Wait for this Worker to
                        # publish its own startup/progress before judging idle.
                        idle_seconds = None
                except (TypeError, ValueError):
                    idle_seconds = None
            threshold = phase_idle_timeout(phase)
            heartbeat_seconds = heartbeat_for(progress)
            llm_state = (
                str(progress.get("llm", {}).get("state", "idle"))
                if progress and isinstance(progress.get("llm"), dict)
                else "idle"
            )
            # A missing chunk is only actionable while an LLM call is
            # explicitly open.  Startup/cleanup phases and a terminal LLM
            # state must never be classified as provider silence.
            monitorable_phase = phase in {
                "llm_streaming", "llm_request", "running_tool", "running_sandbox"
            } and (phase not in {"llm_streaming", "llm_request"} or llm_state in {"started", "streaming"})
            progress_marker = (
                str(progress.get("last_progress_at", "")) if progress else None
            )
            # A provider stall is only a suspicion.  Any real progress after
            # suspicion starts a fresh observation window; otherwise one slow
            # call followed by a successful tool/LLM event could still be
            # killed by the old grace timer.
            if (
                idle_suspected_at is not None
                and progress_marker
                and progress_marker != idle_progress_marker
            ):
                idle_suspected_at = None
                idle_phase = None
                idle_timeout = None
                idle_progress_marker = None
            elif idle_suspected_at is not None and not monitorable_phase:
                idle_suspected_at = None
                idle_phase = None
                idle_timeout = None
                idle_progress_marker = None
            elif idle_suspected_at is not None and idle_seconds is not None and idle_seconds < threshold:
                idle_suspected_at = None
                idle_phase = None
                idle_timeout = None
                idle_progress_marker = None
            # Provider silence is diagnostic first. A live Worker may be blocked
            # inside a slow provider call, and an in-process heartbeat can itself
            # be paused by that provider/runtime. Only a continuous lack of
            # real progress through the explicit grace period is terminating;
            # the heartbeat is never used as proof of progress.
            if monitorable_phase and idle_suspected_at is None and idle_seconds is not None and idle_seconds >= threshold:
                idle_suspected_at = elapsed
                idle_phase = phase
                idle_timeout = threshold
                idle_progress_marker = progress_marker
                progress_store.request_provider_stall(
                    trace_id,
                    reason=f"{phase} 阶段超过 {threshold:g} 秒没有真实进度",
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
                        str(progress.get("work_item_id", "worker")) if progress else "worker",
                        "worker_idle_suspected",
                        details={
                            "phase": phase,
                            "idle_seconds": idle_seconds,
                            "idle_timeout_seconds": threshold,
                            "grace_seconds": provider_stall_grace_seconds,
                        },
                    )
                    TraceStore(project_path).record_event(
                        context_data,
                        str(progress.get("work_item_id", "worker")) if progress else "worker",
                        "provider_stalled",
                        details={
                            "phase": phase,
                            "idle_seconds": idle_seconds,
                            "heartbeat_seconds": heartbeat_seconds,
                            "action": "provider_stall_recorded_no_worker_cancel",
                        },
                    )
                except Exception:
                    pass
            if (
                idle_suspected_at is not None
                and elapsed - idle_suspected_at >= provider_stall_grace_seconds
            ):
                self._stop_process(process)
                traces = TraceStore(project_path)
                try:
                    trace = traces.load_trace(trace_id)
                    from app.orchestration.trace import TraceContext
                    context_data = TraceContext(
                        requirement_id=str(trace["requirement_id"]), trace_id=trace_id,
                        parent_trace_id=trace.get("parent_trace_id"),
                    )
                    traces.record_event(
                        context_data, "worker", "provider_stall_timeout",
                        details={"phase": idle_phase, "idle_timeout_seconds": idle_timeout,
                                 "grace_seconds": provider_stall_grace_seconds},
                    )
                    traces.finish_trace(
                        context_data, "failed",
                        error=(f"Provider 在 {provider_stall_grace_seconds:g} 秒宽限期内无进度，"
                               "已终止当前 Worker，可从 checkpoint 重试"),
                    )
                except Exception:
                    pass
                with self._lock:
                    self._processes.pop(trace_id, None)
                return "failed"
        if process.is_alive():
            self._stop_process(process)
            traces = TraceStore(project_path)
            try:
                trace = traces.load_trace(trace_id)
                from app.orchestration.trace import TraceContext

                context_data = TraceContext(
                    requirement_id=str(trace["requirement_id"]),
                    trace_id=trace_id,
                    parent_trace_id=trace.get("parent_trace_id"),
                )
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                event_type = "worker_timed_out"
                traces.record_event(
                    context_data,
                    "worker",
                    event_type,
                    details={
                        "timeout_seconds": self._worker_timeout_seconds,
                        "elapsed_seconds": elapsed,
                        "phase": idle_phase,
                        "idle_timeout_seconds": idle_timeout,
                    },
                )
                traces.finish_trace(
                    context_data,
                    "failed",
                    error=f"Worker 执行超过 {self._worker_timeout_seconds:g} 秒，已终止",
                )
            except Exception:
                pass
            result = "failed"
        else:
            traces = TraceStore(project_path)
            try:
                if process.exitcode == 75:
                    # A concurrent resume lost the persistent Worker lease;
                    # leave the owner's Trace state untouched.
                    result = "duplicate"
                    with self._lock:
                        self._processes.pop(trace_id, None)
                    return result
                payload = traces.load_trace(trace_id)
                result = str(payload.get("status", "failed"))
                if result in {"planned", "running"}:
                    from app.orchestration.trace import TraceContext

                    context_data = TraceContext(
                        requirement_id=str(payload["requirement_id"]),
                        trace_id=trace_id,
                        parent_trace_id=payload.get("parent_trace_id"),
                    )
                    traces.record_event(
                        context_data,
                        "worker",
                        "worker_failed",
                        details={
                            "pid": process.pid,
                            "exitcode": process.exitcode,
                            "error": "Worker 在未写入终态前退出",
                        },
                    )
                    traces.finish_trace(
                        context_data,
                        "failed",
                        error="Worker 在未写入终态前退出",
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
                next_item = replace(
                    item,
                    allowed_paths=tuple(paths),
                    forbidden_paths=forbidden,
                    contract_digest=None,
                )
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
                for artifact_key in node.input_artifacts
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

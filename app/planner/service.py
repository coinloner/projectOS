"""Planner v0 服务：构造上下文、请求草案、校验并执行一次修复。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os

from app.agent.registry import AgentRegistry
from app.artifact.store import ArtifactStore
from app.memory.store import MemoryStore
from app.memory.context import MemoryContextAssembler
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft, PlanDraftError
from app.planner.errors import PlanValidationError
from app.planner.planner import PlannerRuntime
from app.planner.validator import PlanValidator
from app.planner.patch import (
    AppliedPlanPatch,
    PlanPatch,
    PlanPatchError,
    RepairPlanPatch,
    apply_repair_patch,
    apply_patch,
)
from app.workflow.template import WorkflowTemplateRegistry
from app.orchestration.plan import ExecutionPlan
from app.orchestration.retry import (
    FailureKind,
    FailurePackage,
    FailureSignal,
    RecoveryAction,
    RetryLedger,
    RetryPolicy,
    RetryRecord,
    RetryScope,
    repair_protocol_prompt,
)
from app.orchestration.progress import (
    ExecutionActivity,
    ExecutionLifecycle,
    ExecutionOutcome,
    WorkerProgressStore,
    track_planning_stream,
)
from app.orchestration.trace import TraceStore


@dataclass(frozen=True)
class PlannerResult:
    """一次成功规划的可展示结果。"""

    plan: ExecutionPlan
    draft: PlanDraft
    context: PlanningContext
    attempts: int


@dataclass(frozen=True)
class RepairPlannerResult:
    """一次受限失败恢复规划结果，不携带旧 PlanDraft。"""

    plan: ExecutionPlan
    patch: RepairPlanPatch
    context: PlanningContext
    attempts: int


class PlannerFailure(RuntimeError):
    """Planner 在一次修复后仍无法产出合法计划。"""

    def __init__(
        self,
        message: str,
        *,
        trace_id: str | None = None,
        signal: FailureSignal | None = None,
    ) -> None:
        super().__init__(message)
        self.trace_id = trace_id
        self.signal = signal


class PlannerService:
    """ProjectOS 的计划入口，不向 Planner 暴露可执行对象。"""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        agents: AgentRegistry,
        templates: WorkflowTemplateRegistry,
        artifacts: ArtifactStore,
        validator: PlanValidator | None = None,
        traces: TraceStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self._runtime = runtime
        self._agents = agents
        self._templates = templates
        self._artifacts = artifacts
        self._validator = validator or PlanValidator()
        self._traces = traces or TraceStore(artifacts.project_path)
        self._memory = memory or MemoryStore(artifacts.project_path)
        self._memory_context = MemoryContextAssembler(self._memory)

    def plan(self, *, goal: str, plan_id: str) -> PlannerResult:
        goal = _require_goal(goal)
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        prompt = _planning_prompt(context)
        trace = self._traces.start_trace(goal)
        # Planner runs in the API process (before the spawned delivery Worker),
        # so point Responses diagnostics at this Trace as well.  The Worker
        # refreshes the same setting when it starts.
        os.environ["PROJECTOS_SSE_DIAGNOSTICS_PATH"] = str(
            self._traces.trace_root(trace.trace_id) / "sse-diagnostics.jsonl"
        )
        progress = WorkerProgressStore(self._artifacts.project_path)
        self._traces.mark_planning(trace.trace_id)
        self._traces.record_event(
            trace,
            "planning",
            "planning_started",
            details={"plan_id": plan_id},
        )
        progress.record_planning_state(
            trace.trace_id,
            lifecycle=ExecutionLifecycle.RUNNING,
            activity=ExecutionActivity.WORKER,
            event="planning_started",
            summary="规划阶段开始",
            details={"plan_id": plan_id},
        )
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal,
        )

        previous_failure: FailureSignal | None = None
        for attempt in (1, 2):
            self._traces.record_event(
                trace,
                "planning",
                "planning_attempt_started",
                details={"attempt": attempt, "plan_id": plan_id},
            )
            progress.record_planning_state(
                trace.trace_id,
                lifecycle=(
                    ExecutionLifecycle.RETRYING
                    if attempt > 1
                    else ExecutionLifecycle.RUNNING
                ),
                activity=ExecutionActivity.LLM,
                event="planning_attempt_started",
                summary=("规划请求重试" if attempt > 1 else "规划请求已发送"),
                attempt=attempt,
                details={"plan_id": plan_id},
            )
            self._memory.append(
                trace_id=trace.trace_id,
                role="system",
                event_type="planner_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                # Planner uses a direct Responses call (no CrewAI Agent), so
                # bind its otherwise agent-less SSE callbacks to this trace.
                # The scope records chunk/byte facts only and is safe for
                # concurrent planning requests.
                with track_planning_stream(progress, trace.trace_id, attempt=attempt) as stream_tracker:
                    try:
                        raw_draft = self._runtime.generate(prompt)
                    except Exception as error:
                        # CrewAI dispatches non-chunk event callbacks in a
                        # background executor.  The direct call's exception
                        # is the authoritative immediate terminal fact.
                        stream_tracker.llm_failed(
                            type("PlanningLLMError", (), {"error": str(error), "call_id": None})()
                        )
                        raise
                    else:
                        # Ditto for completion: record it before planning can
                        # move on, rather than relying on an eventually-run
                        # CrewAI callback to update the status snapshot.
                        stream_tracker.llm_completed(
                            type("PlanningLLMComplete", (), {"call_id": None})()
                        )
            except Exception as error:
                signal = _provider_failure_signal(error, prompt)
                self._record_planning_failure(
                    trace,
                    progress,
                    plan_id=plan_id,
                    attempt=attempt,
                    signal=signal,
                    error=error,
                )
                if attempt == 2:
                    self._traces.finish_trace(
                        trace, "failed", error=f"Planner Provider 调用失败: {signal.summary}"
                    )
                    terminal_event = (
                        "planning_retry_circuit_open"
                        if previous_failure is not None
                        and previous_failure.failure_fingerprint == signal.failure_fingerprint
                        else "planning_retry_budget_exhausted"
                    )
                    progress.record_planning_state(
                        trace.trace_id,
                        lifecycle=ExecutionLifecycle.TERMINAL,
                        activity=ExecutionActivity.WORKER,
                        event=terminal_event,
                        summary=(
                            "规划 Provider 同一失败达到上限，已熔断"
                            if terminal_event == "planning_retry_circuit_open"
                            else "规划 Provider 重试预算耗尽"
                        ),
                        outcome=ExecutionOutcome.FAILED,
                        attempt=attempt,
                        details={"plan_id": plan_id, "failure": signal.as_dict()},
                    )
                    raise PlannerFailure(
                        f"Planner Provider 调用失败: {signal.summary}",
                        trace_id=trace.trace_id,
                        signal=signal,
                    ) from error
                previous_failure = signal
                prompt = _planning_prompt(context)
                continue
            if raw_draft is None or not str(raw_draft).strip():
                signal = _provider_failure_signal(None, prompt)
                self._memory.append(
                    trace_id=trace.trace_id,
                    role="planner",
                    event_type="draft_output",
                    content="[provider_empty_response]",
                    attempt=attempt,
                    metadata={"plan_id": plan_id, "empty": True},
                )
                self._record_planning_failure(
                    trace,
                    progress,
                    plan_id=plan_id,
                    attempt=attempt,
                    signal=signal,
                    error=None,
                )
                if attempt == 2:
                    self._traces.finish_trace(
                        trace, "failed", error=f"Planner Provider 调用失败: {signal.summary}"
                    )
                    terminal_event = (
                        "planning_retry_circuit_open"
                        if previous_failure is not None
                        and previous_failure.failure_fingerprint == signal.failure_fingerprint
                        else "planning_retry_budget_exhausted"
                    )
                    progress.record_planning_state(
                        trace.trace_id,
                        lifecycle=ExecutionLifecycle.TERMINAL,
                        activity=ExecutionActivity.WORKER,
                        event=terminal_event,
                        summary=(
                            "规划 Provider 同一空响应达到上限，已熔断"
                            if terminal_event == "planning_retry_circuit_open"
                            else "规划 Provider 重试预算耗尽"
                        ),
                        outcome=ExecutionOutcome.FAILED,
                        attempt=attempt,
                        details={"plan_id": plan_id, "failure": signal.as_dict()},
                    )
                    raise PlannerFailure(
                        f"Planner Provider 调用失败: {signal.summary}",
                        trace_id=trace.trace_id,
                        signal=signal,
                    )
                previous_failure = signal
                prompt = _planning_prompt(context)
                continue
            raw_draft = str(raw_draft)
            self._memory.append(
                trace_id=trace.trace_id,
                role="planner",
                event_type="draft_output",
                content=raw_draft,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                draft = PlanDraft.parse(raw_draft)
                plan = self._validator.validate(
                    draft,
                    context=context,
                    plan_id=plan_id,
                    trace=trace,
                )
                self._traces.record_event(
                    trace,
                    "planning",
                    "planning_completed",
                    details={"plan_id": plan_id, "attempt": attempt},
                )
                progress.record_planning_state(
                    trace.trace_id,
                    lifecycle=ExecutionLifecycle.RUNNING,
                    activity=ExecutionActivity.WORKER,
                    event="planning_completed",
                    summary="规划已生成并通过校验",
                    attempt=attempt,
                    details={"plan_id": plan_id},
                )
                self._traces.mark_planned(trace.trace_id)
                return PlannerResult(
                    plan=plan,
                    draft=draft,
                    context=context,
                    attempts=attempt,
                )
            except (PlanDraftError, PlanValidationError) as error:
                signal = FailureSignal(
                    kind=FailureKind.PLANNER_VALIDATION,
                    summary=str(error),
                    validator=type(error).__name__,
                    input_digest=_digest(prompt),
                    retry_hint="修正草案后重试一次",
                )
                self._record_planning_failure(
                    trace,
                    progress,
                    plan_id=plan_id,
                    attempt=attempt,
                    signal=signal,
                    error=error,
                )
                if attempt == 2:
                    self._traces.finish_trace(trace, "failed", error=str(error))
                    terminal_event = (
                        "planning_retry_circuit_open"
                        if previous_failure is not None
                        and previous_failure.failure_fingerprint == signal.failure_fingerprint
                        else "planning_retry_budget_exhausted"
                    )
                    progress.record_planning_state(
                        trace.trace_id,
                        lifecycle=ExecutionLifecycle.TERMINAL,
                        activity=ExecutionActivity.WORKER,
                        event=terminal_event,
                        summary=(
                            "规划同一失败指纹达到上限，已熔断"
                            if terminal_event == "planning_retry_circuit_open"
                            else "规划重试预算耗尽"
                        ),
                        outcome=ExecutionOutcome.FAILED,
                        attempt=attempt,
                        details={"plan_id": plan_id, "failure": signal.as_dict()},
                    )
                    raise PlannerFailure(
                        f"Planner 在修复后仍无法生成合法计划: {error}",
                        trace_id=trace.trace_id,
                        signal=signal,
                    ) from error
                previous_failure = signal
                prompt = _repair_prompt(context, raw_draft, str(error))

        raise AssertionError("Planner 修复循环未按预期结束")

    def _record_planner_retry(
        self,
        trace,
        *,
        plan_id: str,
        attempt: int,
        signal: FailureSignal,
    ) -> RetryRecord | None:
        """Record one planning/replan decision in the Trace retry ledger.

        ``scope`` keeps planner retries distinct from WorkItem retries while
        the ledger still provides one durable budget and one failure taxonomy
        across the whole run. The bounded loops remain the final execution
        guard; this record makes their decision recoverable and inspectable.
        """
        ledger = RetryLedger(self._artifacts.project_path, trace.trace_id)
        policy = RetryPolicy()
        records = ledger.records()
        subject_records = [
            record
            for record in records
            if record.scope is RetryScope.PLANNING and record.subject_id == plan_id
        ]
        kind_records = [
            record
            for record in subject_records
            if record.failure.kind is signal.kind
        ]
        action = policy.action_for(
            signal,
            total_retries=ledger.budget_count(),
            item_retries=sum(
                record.action in {
                    RecoveryAction.RETRY_ITEM,
                    RecoveryAction.RETRY_BATCH,
                    RecoveryAction.RESUME,
                }
                for record in subject_records
            ),
            kind_retries=sum(
                record.action in {
                    RecoveryAction.RETRY_ITEM,
                    RecoveryAction.RETRY_BATCH,
                    RecoveryAction.RESUME,
                }
                for record in kind_records
            ),
        )
        record = RetryRecord(
            scope=RetryScope.PLANNING,
            subject_id=plan_id,
            attempt=ledger.next_attempt(
                scope=RetryScope.PLANNING,
                subject_id=plan_id,
            ),
            max_attempts=policy.max_attempts_for(signal.kind),
            action=action,
            failure=signal,
        )
        ledger.append(record)
        self._traces.record_event(
            trace,
            "planning",
            "retry_decision_recorded",
            details={"retry_record": record.as_dict()},
        )
        return record

    def _record_planning_failure(
        self,
        trace,
        progress: WorkerProgressStore,
        *,
        plan_id: str,
        attempt: int,
        signal: FailureSignal,
        error: BaseException | None,
    ) -> None:
        retry_record = self._record_planner_retry(
            trace,
            plan_id=plan_id,
            attempt=attempt,
            signal=signal,
        )
        details = {
            "plan_id": plan_id,
            "failure": signal.as_dict(),
            "error_type": type(error).__name__ if error is not None else None,
            "retry_record": retry_record.as_dict() if retry_record is not None else None,
        }
        self._traces.record_event(
            trace,
            "planning",
            "planning_failed",
            details=details,
        )
        progress.record_planning_state(
            trace.trace_id,
            lifecycle=ExecutionLifecycle.RETRYING if attempt < 2 else ExecutionLifecycle.TERMINAL,
            activity=ExecutionActivity.LLM,
            event="planning_failed",
            summary=signal.summary,
            outcome=ExecutionOutcome.FAILED if attempt >= 2 else None,
            attempt=attempt,
            details=details,
        )
        if attempt < 2:
            self._traces.record_event(
                trace,
                "planning",
                "planning_retrying",
                details={"attempt": attempt + 1, "failure": signal.as_dict()},
            )
            progress.record_planning_state(
                trace.trace_id,
                lifecycle=ExecutionLifecycle.RETRYING,
                activity=ExecutionActivity.WORKER,
                event="planning_retrying",
                summary="规划失败，准备有限重试",
                attempt=attempt + 1,
                details={"plan_id": plan_id, "failure": signal.as_dict()},
            )

    def plan_controlled_workflow(
        self, *, goal: str, plan_id: str, workflow_id: str
    ) -> PlannerResult:
        """直接编译一个由调用方明确选择的受控 Workflow。

        API/CLI 可以选择已注册的流程模板，但不能提交其中的 WorkItem 授权字段。
        这类模板不需要 LLM 再次拆解节点，避免把 slot、发布目标等控制面权限交给模型。
        """
        goal = _require_goal(goal)
        template = self._templates.get(workflow_id)
        if template is None:
            raise PlannerFailure(f"未注册 Workflow: '{workflow_id}'")
        if not template.has_controlled_execution:
            raise PlannerFailure(
                f"Workflow '{workflow_id}' 不是受控执行模板，不能由该入口直接运行"
            )
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        trace = self._traces.start_trace(goal)
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal,
        )
        draft = PlanDraft.model_validate(
            {
                "rationale": f"调用方明确选择受控 Workflow: {workflow_id}",
                "template_hint_id": workflow_id,
                "steps": [],
            }
        )
        try:
            plan = self._validator.validate(
                draft,
                context=context,
                plan_id=plan_id,
                trace=trace,
            )
        except PlanValidationError as error:
            raise PlannerFailure(f"受控 Workflow 无法生成计划: {error}") from error
        self._memory.append(
            trace_id=trace.trace_id,
            role="system",
            event_type="planner_input",
            content=f"controlled_workflow={workflow_id}",
            attempt=1,
            metadata={"plan_id": plan_id, "controlled": True},
        )
        self._memory.append(
            trace_id=trace.trace_id,
            role="planner",
            event_type="draft_output",
            content=draft.model_dump_json(),
            attempt=1,
            metadata={"plan_id": plan_id, "controlled": True},
        )
        return PlannerResult(
            plan=plan,
            draft=draft,
            context=context,
            attempts=0,
        )

    def plan_patch(
        self,
        *,
        previous_plan: ExecutionPlan,
        change_request: str,
        completed_work_item_ids: set[str] | frozenset[str] = frozenset(),
        allow_completed_revision: bool = False,
        plan_id: str | None = None,
    ) -> AppliedPlanPatch:
        """只为既有计划生成局部补丁，不重新规划整张 DAG。"""
        if not isinstance(change_request, str) or not change_request.strip():
            raise PlannerFailure("修改请求不能为空")
        patch_plan_id = plan_id or f"{previous_plan.id}-patch"
        prompt = _patch_prompt(
            previous_plan,
            change_request.strip(),
            completed_work_item_ids,
        )
        for attempt in (1, 2):
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="system",
                event_type="planner_patch_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": patch_plan_id},
            )
            try:
                raw_patch = self._runtime.generate(prompt)
            except Exception as error:
                signal = _provider_failure_signal(error, prompt)
                self._record_planner_retry(
                    previous_plan.trace,
                    plan_id=patch_plan_id,
                    attempt=attempt,
                    signal=signal,
                )
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner Provider 在局部修改后仍失败: {signal.summary}",
                        trace_id=previous_plan.trace.trace_id,
                        signal=signal,
                    ) from error
                prompt = _patch_repair_prompt(prompt, "", signal.summary)
                continue
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="planner",
                event_type="patch_output",
                content=raw_patch,
                attempt=attempt,
                metadata={"plan_id": patch_plan_id},
            )
            try:
                patch = PlanPatch.parse(raw_patch)
                return apply_patch(
                    previous_plan,
                    patch,
                    agents=self._agents,
                    completed_work_item_ids=completed_work_item_ids,
                    allow_completed_revision=allow_completed_revision,
                )
            except PlanPatchError as error:
                signal = FailureSignal(
                    kind=FailureKind.PLANNER_VALIDATION,
                    summary=str(error),
                    validator=type(error).__name__,
                    input_digest=_digest(prompt),
                    retry_hint="修正局部补丁后重试一次",
                )
                self._record_planner_retry(
                    previous_plan.trace,
                    plan_id=patch_plan_id,
                    attempt=attempt,
                    signal=signal,
                )
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在局部修改后仍无法生成合法补丁: {error}",
                        trace_id=previous_plan.trace.trace_id,
                        signal=signal,
                    ) from error
                prompt = _patch_repair_prompt(prompt, raw_patch, str(error))
        raise AssertionError("Planner patch 修复循环未按预期结束")

    def plan_repair(
        self,
        *,
        previous_plan: ExecutionPlan,
        failure: FailureSignal,
        plan_id: str,
        repair_scope: tuple[str, ...] = (),
    ) -> RepairPlannerResult:
        """为可信失败信号生成受限追加补丁，不修改原交付 DAG。"""
        context = PlanningContext.build(
            goal=previous_plan.goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        package = self._traces.failure_package(previous_plan.trace, failure)
        package = replace(package, repair_scope=repair_scope)
        prompt = _repair_planning_prompt(
            context,
            previous_plan,
            package,
            repair_scope=repair_scope,
            memory_context=self._memory_context.build(
                trace_id=previous_plan.trace.trace_id,
                work_item_id=None,
                query=f"{previous_plan.goal} {failure.summary}",
                include_durable=True,
            ).as_prompt(),
        )
        for attempt in (1, 2):
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="system",
                event_type="planner_repair_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                raw_draft = self._runtime.generate(prompt)
            except Exception as error:
                signal = _provider_failure_signal(error, prompt)
                self._record_planner_retry(
                    previous_plan.trace,
                    plan_id=plan_id,
                    attempt=attempt,
                    signal=signal,
                )
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner Provider 在修复规划后仍失败: {signal.summary}",
                        trace_id=previous_plan.trace.trace_id,
                        signal=signal,
                    ) from error
                prompt = _repair_planning_retry_prompt(
                    context,
                    previous_plan,
                    package,
                    "",
                    signal.summary,
                    repair_scope=repair_scope,
                    memory_context=self._memory_context.build(
                        trace_id=previous_plan.trace.trace_id,
                        work_item_id=None,
                        query=f"{previous_plan.goal} {failure.summary}",
                        include_durable=True,
                    ).as_prompt(),
                )
                continue
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="planner",
                event_type="repair_patch_output",
                content=raw_draft,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                patch = RepairPlanPatch.parse(
                    raw_draft,
                    base_plan_id=previous_plan.id,
                    repair_scope=repair_scope,
                )
                repair_domains = {
                    self._agents.definition(operation.agent_id).domain
                    for operation in patch.operations
                    if self._agents.definition(operation.agent_id) is not None
                }
                invalid_domains = repair_domains - {"bootstrap", "code", "test"}
                if invalid_domains:
                    invalid = ", ".join(sorted(invalid_domains))
                    raise PlanValidationError(
                        "修复计划不能包含 integration/review/planning Agent: " + invalid
                    )
                code_repair_paths = package.repair_paths or package.owner_files
                if "code" in repair_domains and not code_repair_paths:
                    raise PlanValidationError(
                        "代码修复缺少控制面归因的 repair_paths/owner_files；"
                        "不得派发无文件范围的 CodeAgent"
                    )
                plan = apply_repair_patch(
                    previous_plan,
                    patch,
                    agents=self._agents,
                    plan_id=plan_id,
                )
                plan = self._attach_failure_package(plan, package)
                return RepairPlannerResult(
                    plan=plan,
                    patch=patch,
                    context=context,
                    attempts=attempt,
                )
            except (PlanPatchError, PlanDraftError, PlanValidationError) as error:
                signal = FailureSignal(
                    kind=FailureKind.PLANNER_VALIDATION,
                    summary=str(error),
                    validator=type(error).__name__,
                    input_digest=_digest(prompt),
                    retry_hint="修正修复补丁后重试一次",
                )
                retry_record = self._record_planner_retry(
                    previous_plan.trace,
                    plan_id=plan_id,
                    attempt=attempt,
                    signal=signal,
                )
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在修复计划后仍无法生成合法计划: {error}",
                        trace_id=previous_plan.trace.trace_id,
                        signal=signal,
                    ) from error
                # 修复规划的重试必须继续携带局部修复约束。普通的
                # ``_repair_prompt`` 只适合初始计划，会丢失“禁止 review /
                # integration”等控制面边界，导致模型在第二次尝试再次生成
                # 无法执行的交付节点。
                prompt = _repair_planning_retry_prompt(
                    context,
                    previous_plan,
                    package,
                    raw_draft,
                    str(error),
                    repair_scope=repair_scope,
                    memory_context=self._memory_context.build(
                        trace_id=previous_plan.trace.trace_id,
                        work_item_id=None,
                        query=f"{previous_plan.goal} {failure.summary}",
                        include_durable=True,
                    ).as_prompt(),
                )
        raise AssertionError("Planner repair 循环未按预期结束")

    def _attach_failure_package(
        self, plan: ExecutionPlan, package: FailurePackage
    ) -> ExecutionPlan:
        work_items = []
        for item in plan.work_items:
            definition = self._agents.definition(item.agent_id)
            if definition is None or definition.domain not in {"code", "test"}:
                work_items.append(item)
                continue
            # A repair WorkItem is newly appended and has no inherited
            # implementation contract.  Its write scope must therefore come
            # exclusively from concrete control-plane failure attribution,
            # never from planner text or an empty fallback.
            repair_paths = package.repair_paths or package.owner_files
            forbidden_rework = package.forbidden_rework or item.forbidden_paths
            # EXCLUSIVE CodeAgent repairs must never touch tests or control-plane
            # metadata. Keep this deterministic even when the Planner omits paths.
            if definition.domain == "code":
                forbidden_rework = tuple(dict.fromkeys((*forbidden_rework,
                    "tests/**", ".projectos/**", "project.yaml", "runtime.yaml")))
            scoped = replace(
                package,
                repair_paths=repair_paths,
                forbidden_rework=forbidden_rework,
                unsatisfied_constraints=item.acceptance_criteria,
                satisfied_constraints=tuple(
                    criterion for criterion in item.constraints
                    if criterion not in item.acceptance_criteria
                ),
                owner_files=item.owned_files or item.required_paths,
            )
            if definition.domain == "code" and repair_paths:
                revised_item = replace(
                    item,
                    allowed_paths=tuple(repair_paths),
                    forbidden_paths=tuple(forbidden_rework),
                    # Path scope is deterministically narrowed/expanded by
                    # the control plane from FailurePackage evidence. Seal a
                    # fresh digest for this repaired contract.
                    contract_digest=None,
                )
                item.validate_scope_transition(revised_item)
                item = revised_item
            work_items.append(replace(item, failure_package=scoped))
        return replace(plan, work_items=work_items)


def _require_goal(goal: str) -> str:
    if not isinstance(goal, str) or not goal.strip():
        raise PlannerFailure("Planner goal 不能为空")
    return goal.strip()


def _digest(value: str) -> str:
    """Return a stable, non-reversible identifier for a planning input."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_failure_signal(
    error: BaseException | None,
    prompt: str,
) -> FailureSignal:
    """Normalize provider transport/empty responses without persisting secrets."""
    if error is None:
        return FailureSignal(
            kind=FailureKind.PROVIDER_EMPTY_RESPONSE,
            summary="LLM Provider 返回 None 或空响应",
            input_digest=_digest(prompt),
            retry_hint="使用同一规划输入重试一次",
        )
    summary = f"{type(error).__name__}: {error}".replace("\n", " ").strip()
    return FailureSignal(
        kind=FailureKind.PROVIDER_TRANSPORT,
        summary=summary[:240],
        input_digest=_digest(prompt),
        retry_hint="使用同一规划输入重试一次",
    )


def _patch_prompt(
    plan: ExecutionPlan,
    change_request: str,
    completed_work_item_ids: set[str] | frozenset[str],
) -> str:
    items = [
        {
            "id": item.id,
            "agent_id": item.agent_id,
            "objective": item.objective,
            "depends_on": list(item.dependency_ids),
        }
        for item in plan.work_items
    ]
    return (
        "请只为现有计划生成 PlanPatch JSON，不要重新生成完整计划。\n\n"
        f"base_plan_id: {plan.id}\n"
        f"用户修改请求：{change_request}\n"
        f"已完成 WorkItem（历史结果不可覆写，但本次补丁可创建新 revision 重新计算）：{sorted(completed_work_item_ids)}\n"
        f"现有 WorkItem：{json.dumps(items, ensure_ascii=False)}\n\n"
        "只允许 operation=modify/add/remove。modify 只能修改 objective，不能修改 dependencies；"
        "add 必须提供 ref、agent_id、objective 和 depends_on；remove 必须提供 work_item_id。"
        "不允许改变 Agent 权限、execution_mode、artifact、slot、发布目标或质量门。"
        "最多 modify 2 个、add 2 个、remove 1 个节点；只输出 JSON。\n"
        '{"schema_version":1,"rationale":"...","base_plan_id":"...","operations":['
        '{"operation":"modify","work_item_id":"...","objective":"..."}]}'
    )


def _patch_repair_prompt(previous_prompt: str, raw_patch: str, error: str) -> str:
    return (
        "上一份 PlanPatch 无法通过确定性边界校验。请只输出修正后的 PlanPatch JSON。\n\n"
        f"原始约束：\n{previous_prompt}\n\n"
        f"上一份补丁：\n{raw_patch}\n\n"
        f"校验错误：\n{error}\n"
    )


def _planning_prompt(context: PlanningContext) -> str:
    return (
        "请为以下 ProjectOS 上下文生成一份完整的 PlanDraft JSON。\n\n"
        f"{_plan_draft_protocol_prompt()}\n\n"
        "上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        "只输出上述新协议的一个 JSON 对象，不要输出 Markdown、解释或其它 envelope。"
    )


def _repair_prompt(context: PlanningContext, invalid_draft: str, error: str) -> str:
    return (
        "上一份 PlanDraft 无法通过确定性解析或校验。请保留原目标并输出一份完整、独立的修正后 PlanDraft JSON。\n\n"
        f"{_plan_draft_protocol_prompt()}\n\n"
        f"规划上下文：\n{context.as_prompt_json()}\n\n"
        "上一份草案仅作为错误诊断资料，不能沿用它的 envelope、字段或执行控制信息：\n"
        f"{invalid_draft}\n\n"
        f"校验错误：\n{error}\n\n"
        "请根据错误重新生成完整的新协议对象；不要输出 Markdown 或解释。"
    )


def _plan_draft_protocol_prompt() -> str:
    """Return the single Planner output contract used by initial and retry prompts.

    Keeping this contract in one place is intentional: a retry must not silently
    re-introduce the legacy Responses envelope after the first output is rejected.
    """

    return (
        "Planner 只有一个受控输出协议。必填字段为 rationale 和 steps；"
        "可选字段为 process_id、template_hint_id、template_dependency_overrides。\n"
        "模板只能通过 template_hint_id 引用，步骤只能放在 steps 中；"
        "不要输出 node id、output key、工具、权限、路径或执行模式。\n"
        "严禁输出旧协议或执行控制字段：kind、type、version、template_id、nodes、"
        "execution_mode、slot、publish_target、capability、tool_call。\n"
        "合法形状示例：\n"
        '{"rationale":"为什么选择这些步骤",'
        '"process_id":"software_delivery",'
        '"template_hint_id":null,'
        '"steps":[{"ref":"requirement","agent_id":"requirement_agent",'
        '"objective":"整理用户需求","depends_on":[]}],'
        '"template_dependency_overrides":[]}'
    )


def _repair_planning_prompt(
    context: PlanningContext,
    previous_plan: ExecutionPlan,
    package: FailurePackage,
    *,
    repair_scope: tuple[str, ...] = (),
    memory_context: str = "",
) -> str:
    previous_steps = [
        {
            "work_item_id": item.id,
            "agent_id": item.agent_id,
            "objective": item.objective,
        }
        for item in previous_plan.work_items
    ]
    scope_text = (
        "本次允许影响的局部节点：" + ", ".join(repair_scope) + "\n\n"
        if repair_scope else ""
    )
    return (
        "请为一次受控失败生成追加 RepairPlanPatch JSON。\n"
        + repair_protocol_prompt()
        + "\n\n"
        f"base_plan_id: {previous_plan.id}\n"
        "失败信号（可信控制面数据）：\n"
        f"{json.dumps(package.as_planner_data(), ensure_ascii=False)}\n\n"
        "历史计划摘要（只读，不能修改或复用其 WorkItem id）：\n"
        f"{previous_steps!r}\n\n"
        + scope_text
        + "当前可用控制面上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        + (f"历史会话记忆：\n{memory_context}\n\n" if memory_context else "")
        + "只输出新的 RepairPlanPatch JSON，格式为："
        + '{"schema_version":1,"rationale":"...","base_plan_id":"'
        + previous_plan.id
        + '","repair_scope":'
        + json.dumps(list(repair_scope), ensure_ascii=False)
        + ','
        + '"failure_kind":"<kind>","verification":["运行指定检查并确认新证据通过"],'
        + '"operations":[{"operation":"add","ref":"fix","agent_id":"code_agent",'
        + '"objective":"通过 write_workspace_file 修复实现","depends_on":[]}]}'
        + "。operations 只能使用 operation=add，depends_on 只能引用同一补丁中更早的 ref；目标必须针对失败"
        + "进行修复或再验证；不要创建工具、修改权限、复用历史 step ref，或编写业务代码。"
        + "修复计划只允许 code_agent、test_agent；只有 sandbox/environment 故障时才可加入 "
        + "bootstrap_agent。禁止 code_integration_agent、review_agent、architecture_agent 和 task_agent。"
        + "修复步骤必须产生实际变更：code_agent 的修复步骤必须在 objective 中明确要求"
        + "通过 write_workspace_file 实际修改 workspace 文件；test_agent 的修复步骤必须"
        + "通过 write_test_file 修改测试。只读诊断不构成修复步骤。"
        + "operations 总数最多 10 个；verification 至少描述一个可观察的工具/测试证据。"
    )


def _repair_planning_retry_prompt(
    context: PlanningContext,
    previous_plan: ExecutionPlan,
    package: FailurePackage,
    invalid_draft: str,
    error: str,
    *,
    repair_scope: tuple[str, ...] = (),
    memory_context: str = "",
) -> str:
    """保留修复边界的第二次 Planner 提示。

    修复计划不是普通的动态规划：它只能追加局部的环境、代码和测试步骤。
    重试时把上一份草案和控制面校验错误作为诊断输入，但重新声明完整的
    修复协议，避免模型因通用纠错提示而重新加入 review/integration 节点。
    """
    return (
        _repair_planning_prompt(
            context,
            previous_plan,
            package,
            repair_scope=repair_scope,
            memory_context=memory_context,
        )
        + "\n\n上一份修复草案（不可信，仅用于定位校验错误）：\n"
        + invalid_draft
        + "\n\n控制面校验错误：\n"
        + error
        + "\n\n请重新输出一份满足上述修复协议和 RepairPlanPatch 数量上限的 JSON；"
        + "不要保留被拒绝的 Agent。"
    )

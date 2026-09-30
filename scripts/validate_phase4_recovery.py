"""Phase 4: injected failures observed through the real Runner/Coordinator.

All traces live in an isolated project directory. This never sends a Provider
request, edits the original Live Trace, or claims that a mocked failure is Live.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.application.runs import RunCoordinator
from app.domain.architecture.implementation_contract import (
    ImplementationContract, ProjectContractStore, ImplementationUnit, InterfaceContract,
)
from app.domain.code.git_service import GitCodeIntegrationService, GitCodeStagingService
from app.domain.code.service import CodeIntegrationService
from app.policy.quality import ProjectRuntimePreflight
from app.sandbox.result import SandboxResult, SandboxStatus
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.node_result import NodeResult, NodeStatus
from app.orchestration.plan import ExecutionPlan
from app.orchestration.retry import FailureKind, FailureSignal
from app.orchestration.runner import GraphRunner, GraphRunStatus, _integration_owner_work_items
from app.orchestration.state import RunState
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.tool_manager.gateway import ToolGateway


class AgentSequence:
    def __init__(self, *responses):
        self.responses = responses
        self.calls = 0

    def run(self, task, *, context=None):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        value = self.responses[index]
        if isinstance(value, Exception):
            raise value
        return value


def _dep(item):
    return WorkItemDependency(item.id, DependencySource.SYSTEM)


def _completed(item):
    return NodeResult.completed(work_item_id=item.id, agent_id=item.agent_id, content=item.id)


def _events(traces, trace):
    return list(traces.list_events(trace.trace_id))


def _simple_case(root: Path, name: str, responses, *, agent_id="code_agent", expected_status,
                 expected_calls: int, expected_kind: FailureKind | None = None):
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    traces = TraceStore(str(project))
    trace = traces.start_trace(name)
    owner = WorkItem(id="wi-owner", agent_id=agent_id, objective=name, output_key="owner",
                     execution_mode=ExecutionMode.PARTITIONED if agent_id == "code_agent" else ExecutionMode.EXCLUSIVE,
                     slot="implementation-http" if agent_id == "code_agent" else None,
                     owned_files=("backend/app/main.py",) if agent_id == "code_agent" else ())
    sibling = WorkItem(id="wi-sibling", agent_id="requirement_agent", objective="done", output_key="sibling")
    downstream = WorkItem(id="wi-downstream", agent_id="review_agent", objective="later", output_key="downstream",
                          dependencies=(_dep(owner),))
    plan = ExecutionPlan(id="phase4-" + name, goal=name, trace=trace,
                         validation_scope="vertical_slice", work_items=(owner, sibling, downstream))
    state = RunState(plan, node_results={sibling.id: _completed(sibling)}, artifacts={sibling.output_key: sibling.id})
    sequence = AgentSequence(*responses)
    agents = AgentRegistry()
    agents.register(AgentDefinition(agent_id, "code" if agent_id == "code_agent" else "requirement", name, "implementation"), lambda: sequence)
    runner = GraphRunner(agents, ToolGateway(), traces=traces, max_workers=1)
    result = runner.run(plan, state=state)
    events = _events(traces, trace)
    retry_events = [e for e in events if e["type"] == "work_item_retrying"]
    assert result.status == expected_status, (name, result.status, result.error)
    assert sequence.calls == expected_calls, (name, sequence.calls)
    assert len(retry_events) == expected_calls - 1, (name, retry_events)
    assert result.state.node_results[sibling.id].status == NodeStatus.COMPLETED
    assert not any(e.get("work_item_id") in {sibling.id, downstream.id} and e["type"] == "work_item_started" for e in events)
    assert downstream.id not in result.state.node_results
    if expected_kind:
        assert any(e["details"].get("kind") == expected_kind.value for e in retry_events) or (
            result.failure_signal and result.failure_signal.kind == expected_kind
        ), (name, retry_events, result.failure_signal)
    return {
        "case": name, "trace_id": trace.trace_id, "injection_boundary": "Agent.run exception or completed-without-ChangeSet",
        "status": result.status.value, "failure_kind": expected_kind.value if expected_kind else None,
        "agent_calls": sequence.calls, "retry_events": [e["details"] for e in retry_events],
        "before_frontier": [sibling.id], "after_frontier": sorted(result.state.node_results),
        "completed_sibling_preserved": sibling.id in result.state.node_results,
        "downstream_not_started": downstream.id not in result.state.node_results,
        "raw_events": str(project / ".projectos" / "runs" / trace.trace_id / "events.jsonl"),
        "raw_checkpoint": str(project / ".projectos" / "runs" / trace.trace_id / "checkpoint.json"),
    }


def _semantic_diagnostic(root: Path, name: str) -> tuple[str, tuple[str, ...]]:
    project = root / name
    workspace = project / "workspace"
    backend = workspace / "backend/app"
    backend.mkdir(parents=True, exist_ok=True)
    if name == "import-binding":
        (backend / "main.py").write_text("from app.missing_service import MissingService\n", encoding="utf-8")
        expected = ("backend/app/main.py",)
        contract = ImplementationContract(schema_version=1, units=(
            ImplementationUnit("api", "transport", "HTTP API", ("backend/app/main.py",),
                               owned_files=("backend/app/main.py",)),
        ))
        ProjectContractStore(str(project)).save(contract.as_dict())
    else:
        (backend / "main.py").write_text('from fastapi import FastAPI\napp=FastAPI()\n@app.get("/records")\ndef records(): pass\n', encoding="utf-8")
        tests = workspace / "tests/test_records.py"
        tests.parent.mkdir(parents=True)
        tests.write_text('def test_records(base):\n    _ok(base, "GET", "/records/stats")\n', encoding="utf-8")
        expected = ("tests/test_records.py",)
        contract = ImplementationContract(schema_version=1, units=(
            ImplementationUnit("api", "transport", "Records API", ("backend/app/main.py",),
                               owned_files=("backend/app/main.py",), provides_interfaces=("records.api",)),
            ImplementationUnit("tests", "verification", "Consumer", ("tests/test_records.py",),
                               owned_files=("tests/test_records.py",), consumes_interfaces=("records.api",)),
        ), interfaces=(InterfaceContract("records.api", "api", "Records API", "api", owner_file="backend/app/main.py"),))
        ProjectContractStore(str(project)).save(contract.as_dict())
    try:
        GitCodeIntegrationService(str(project)).validate_published_workspace("phase4-diagnostic")
    except RuntimeError as error:
        text = str(error)
        assert "[owner_files: " in text and all(path in text for path in expected), text
        return text, expected
    raise AssertionError(f"{name}: semantic gate accepted invalid source")


def _coordinator_case(root: Path, name: str):
    """Real coordinator + runner dispatch; inject gate result at integration node."""
    diagnostic, expected_files = _semantic_diagnostic(root, name)
    project = root / (name + "-frontier")
    project.mkdir(parents=True, exist_ok=True)
    traces = TraceStore(str(project))
    trace = traces.start_trace(name + " frontier")
    owner = WorkItem(id="wi-owner", agent_id="code_agent", objective="repair", output_key="owner", owned_files=expected_files)
    sibling = WorkItem(id="wi-sibling", agent_id="code_agent", objective="preserve", output_key="sibling", owned_files=("backend/app/unrelated.py",))
    integration = WorkItem(id="wi-integration", agent_id="code_integration_agent", objective="integrate", output_key="integration",
                           dependencies=(_dep(owner), _dep(sibling)))
    runtime = WorkItem(id="wi-runtime", agent_id="review_agent", objective="runtime gate", output_key="runtime",
                       dependencies=(_dep(integration),))
    plan = ExecutionPlan(id="phase4-" + name, goal=name, trace=trace, validation_scope="vertical_slice",
                         work_items=(owner, sibling, integration, runtime))
    state = RunState(plan, node_results={item.id: _completed(item) for item in (owner, sibling, runtime)},
                     artifacts={item.output_key: item.id for item in (owner, sibling, runtime)})
    owner_ids = _integration_owner_work_items(plan, diagnostic)
    assert owner_ids == (owner.id,), (diagnostic, owner_ids)
    traces.record_plan(plan)
    runner = GraphRunner(AgentRegistry(), ToolGateway(), traces=traces)
    executed = []
    def injected_node(current_state, item, **kwargs):
        executed.append(item.id)
        if item.id == integration.id:
            signal = FailureSignal(FailureKind.CODE_DELIVERY_INCOMPLETE,
                                   "代码集成发现实现适配问题：" + diagnostic,
                                   validator="GitCodeIntegrationService", related_work_item_ids=owner_ids)
            return NodeResult.needs_replan(work_item_id=item.id, agent_id=item.agent_id, signal=signal)
        if item.id == owner.id:
            # Stop after observing the first actual re-entry. The owner would
            # need a new ChangeSet before integration/runtime can resume.
            return NodeResult.needs_replan(work_item_id=item.id, agent_id=item.agent_id,
                                           signal=FailureSignal(FailureKind.TOOL_CONTRACT_MISMATCH,
                                                                "injected bounded stop", retryable=False))
        raise AssertionError(f"unrelated node scheduled: {item.id}")
    container = SimpleNamespace(runner=runner, planner=None, traces=traces)
    with patch.object(runner, "_run_item", side_effect=injected_node):
        result = RunCoordinator._run_with_repairs(container, plan, state)
    events = _events(traces, trace)
    scheduled = [e for e in events if e["type"] == "code_wave_repair_scheduled"]
    assert result.status == GraphRunStatus.NEEDS_REPLAN
    assert executed == [integration.id, owner.id], executed
    assert len(scheduled) == 1 and scheduled[0]["details"]["work_item_ids"] == [owner.id]
    assert sibling.id in state.node_results and runtime.id not in state.node_results
    assert state.forced_rerun_work_item_ids == {owner.id}
    checkpoint = state.as_checkpoint()
    return {"case": name, "trace_id": trace.trace_id,
            "injection_boundary": "GitCodeIntegrationService.validate_published_workspace diagnostic -> integration NodeResult",
            "semantic_diagnostic": diagnostic, "owner_files": expected_files, "repair_scope": owner_ids,
            "recovery_entrypoint": "RunCoordinator._run_with_repairs/code_wave_repair_scheduled",
            "executed_work_items": executed, "before_frontier": [owner.id, sibling.id, runtime.id],
            "after_frontier": sorted(state.node_results), "forced_rerun": checkpoint["forced_rerun_work_item_ids"],
            "completed_sibling_preserved": sibling.id in state.node_results, "runtime_not_started": runtime.id not in state.node_results,
            "scheduled_event": scheduled[0],
            "raw_events": str(project / ".projectos" / "runs" / trace.trace_id / "events.jsonl"),
            "raw_checkpoint": str(project / ".projectos" / "runs" / trace.trace_id / "checkpoint.json")}



def _wave_conflict_case(root: Path):
    project = root / "wave-conflict"
    project.mkdir(parents=True, exist_ok=True)
    traces = TraceStore(str(project))
    trace = traces.start_trace("wave conflict")
    def code(name, wave, deps=()):
        return WorkItem(id="wi-" + name, agent_id="code_agent", objective=name, output_key=name,
                        execution_mode=ExecutionMode.PARTITIONED, slot=name, wave=wave,
                        owned_files=("backend/" + name + ".py",), dependencies=deps)
    owner, sibling = code("owner", 0), code("sibling", 0)
    downstream = code("downstream", 1, (_dep(owner), _dep(sibling)))
    plan = ExecutionPlan(id="phase4-wave", goal="freeze", trace=trace,
                         validation_scope="vertical_slice", work_items=(owner, sibling, downstream))
    state = RunState(plan, node_results={i.id: _completed(i) for i in (owner, sibling)},
                     artifacts={i.output_key: i.id for i in (owner, sibling)})
    traces.record_plan(plan)
    runner = GraphRunner(AgentRegistry(), ToolGateway(), traces=traces)
    executed = []
    def stop(current_state, item, **kwargs):
        executed.append(item.id)
        assert item.id == owner.id, item.id
        return NodeResult.needs_replan(work_item_id=item.id, agent_id=item.agent_id,
            signal=FailureSignal(FailureKind.TOOL_CONTRACT_MISMATCH, "bounded stop", retryable=False))
    def conflict(context):
        assert context.work_item_id == "wave-0-integration"
        raise RuntimeError("Git 三方合并存在冲突: backend/owner.py")
    container = SimpleNamespace(runner=runner, planner=None, traces=traces)
    with patch.object(CodeIntegrationService, "integrate_wave", side_effect=conflict), \
         patch.object(GitCodeStagingService, "integrated_commits", return_value=set()), \
         patch.object(runner, "_run_item", side_effect=stop):
        result = RunCoordinator._run_with_repairs(container, plan, state)
    events = _events(traces, trace)
    failed = [e for e in events if e["type"] == "code_wave_integration_failed"]
    scheduled = [e for e in events if e["type"] == "code_wave_repair_scheduled"]
    started = [e.get("work_item_id") for e in events if e["type"] == "work_item_started"]
    assert result.status == GraphRunStatus.NEEDS_REPLAN, (result.status, result.error)
    assert len(failed) == 1 and failed[0]["details"]["affected_work_item_ids"] == [owner.id]
    assert len(scheduled) == 1 and scheduled[0]["details"]["work_item_ids"] == [owner.id]
    assert executed == [owner.id] and downstream.id not in started
    assert sibling.id in state.node_results and downstream.id not in state.node_results
    assert state.forced_rerun_work_item_ids == {owner.id}
    return {"case": "wave-conflict", "trace_id": trace.trace_id, "status": result.status.value,
            "injection_boundary": "CodeIntegrationService.integrate_wave before downstream dispatch",
            "before_frontier": [owner.id, sibling.id], "after_frontier": sorted(state.node_results),
            "executed_work_items": executed, "downstream_not_started": downstream.id not in started,
            "completed_sibling_preserved": sibling.id in state.node_results,
            "affected_work_item_ids": failed[0]["details"]["affected_work_item_ids"],
            "forced_rerun": sorted(state.forced_rerun_work_item_ids),
            "raw_events": str(project / ".projectos/runs" / trace.trace_id / "events.jsonl"),
            "raw_checkpoint": str(project / ".projectos/runs" / trace.trace_id / "checkpoint.json")}


def _runtime_start_failure_case(root: Path):
    project = root / "runtime-start-failure"
    source = project / "workspace/backend/app"
    source.mkdir(parents=True, exist_ok=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "main.py").write_text("raise RuntimeError('injected startup failure')\n", encoding="utf-8")
    preflight = ProjectRuntimePreflight().evaluate(str(project))
    assert preflight.passed, preflight.issues
    start = time.monotonic()
    launched = subprocess.run([sys.executable, "-m", "app.main"], cwd=source.parent,
                              text=True, capture_output=True, timeout=10, check=False)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    assert launched.returncode != 0 and "injected startup failure" in launched.stderr
    traces = TraceStore(str(project))
    trace = traces.start_trace("runtime start failure")
    test = WorkItem(id="wi-test", agent_id="test_agent", objective="runtime", output_key="tests")
    review = WorkItem(id="wi-review", agent_id="review_agent", objective="review", output_key="review",
                      dependencies=(_dep(test),))
    plan = ExecutionPlan(id="phase4-runtime", goal="startup", trace=trace,
                         validation_scope="vertical_slice", work_items=(test, review))
    test_calls, review_calls = [], []
    class Test:
        def run(self, task, *, context=None):
            test_calls.append(context.work_item_id)
            traces.record_sandbox_evidence(context, SandboxResult(
                status=SandboxStatus.SETUP_FAILED, check_id="runtime-launch", runtime_profile="python-stdlib",
                exit_code=launched.returncode, duration_ms=elapsed_ms, stdout=launched.stdout,
                stderr=launched.stderr, message="runtime startup exited nonzero"))
            return AgentResult.completed("test returned completed")
    class Review:
        def run(self, task, *, context=None):
            review_calls.append(context.work_item_id)
            return AgentResult.completed("unexpected")
    agents = AgentRegistry()
    agents.register(AgentDefinition("test_agent", "test", "test", "tests"), Test)
    agents.register(AgentDefinition("review_agent", "review", "review", "review"), Review)
    result = GraphRunner(agents, ToolGateway(), traces=traces, max_workers=1).run(plan)
    evidence = traces.list_sandbox_evidence(ExecutionContext(
        trace_id=trace.trace_id, work_item_id=test.id, agent_id=test.agent_id))
    assert result.status == GraphRunStatus.BLOCKED, (result.status, result.error)
    assert result.failure_signal and result.failure_signal.kind == FailureKind.SANDBOX_SETUP
    assert test_calls == [test.id] and not review_calls and review.id not in result.state.node_results
    assert evidence and evidence[-1].stderr == launched.stderr and evidence[-1].exit_code == launched.returncode
    assert not any(e["type"] == "work_item_started" and e.get("work_item_id") == review.id
                   for e in _events(traces, trace))
    return {"case": "runtime-start-failure", "trace_id": trace.trace_id, "status": result.status.value,
            "injection_boundary": "real subprocess failure -> SandboxEvidence -> GraphRunner Test gate",
            "static_preflight_passed": True, "exit_code": launched.returncode,
            "sandbox_evidence_id": evidence[-1].id, "failure_kind": result.failure_signal.kind.value,
            "review_not_started": not review_calls,
            "raw_events": str(project / ".projectos/runs" / trace.trace_id / "events.jsonl"),
            "raw_checkpoint": str(project / ".projectos/runs" / trace.trace_id / "checkpoint.json")}


def run_closure_validation(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    cases = [_wave_conflict_case(root), _runtime_start_failure_case(root)]
    return {"schema_version": 1, "status": "passed", "isolation": str(root),
            "method": "isolated mock Git conflict plus real Python startup failure; not a Live Provider run",
            "cases": cases, "summary": {"passed": len(cases), "total": len(cases)}}


def run_validation(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    rows.append(_simple_case(root, "provider-403", (RuntimeError("Error code: 403 - {'error': {'code': 'upstream_policy_rejected'}}"),),
                             expected_status=GraphRunStatus.NEEDS_REPLAN, expected_calls=1, expected_kind=FailureKind.PROVIDER_POLICY_REJECTED))
    for status in (502, 503):
        rows.append(_simple_case(root, f"codeagent-{status}",
                                 (RuntimeError(f"Error code: {status} - upstream unavailable"),)*2,
                                 expected_status=GraphRunStatus.FAILED, expected_calls=2,
                                 expected_kind=FailureKind.PROVIDER_TRANSPORT))
    rows.append(_simple_case(root, "terminal-missing", (RuntimeError("stream ended without terminal signal"),)*2,
                             expected_status=GraphRunStatus.FAILED, expected_calls=2, expected_kind=FailureKind.PROVIDER_TERMINAL_MISSING))
    rows.append(_simple_case(root, "no-changeset", (AgentResult.completed("done"),)*3,
                             expected_status=GraphRunStatus.FAILED, expected_calls=3, expected_kind=FailureKind.CODE_DELIVERY_INCOMPLETE))
    rows.append(_coordinator_case(root, "import-binding"))
    rows.append(_coordinator_case(root, "http-contract"))
    return {"schema_version": 1, "status": "passed", "isolation": str(root),
            "method": "isolated injected Agent/Git gate failure through GraphRunner and RunCoordinator; never a Live Provider call",
            "cases": rows, "summary": {"passed": len(rows), "total": len(rows)}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--closure-only", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    root = output.parent / (output.stem + "-traces")
    result = run_closure_validation(root) if args.closure_only else run_validation(root)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

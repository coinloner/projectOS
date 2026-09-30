"""Regression coverage for Phase 4's observed local-recovery boundaries."""
from app.orchestration.node_result import NodeResult
from app.orchestration.plan import ExecutionPlan
from app.orchestration.retry import FailureKind
from app.orchestration.runner import _provider_failure_kind
from app.orchestration.state import RunState
from app.orchestration.work_item import WorkItem
from scripts.validate_phase4_recovery import run_closure_validation, run_validation


def test_openai_compatible_502_503_are_provider_transport_not_agent_runtime():
    for status in (502, 503):
        assert _provider_failure_kind(RuntimeError(
            f"❌ CrewAI Agent 执行失败: Error code: {status} - upstream unavailable"
        )) is FailureKind.PROVIDER_TRANSPORT
    assert _provider_failure_kind(RuntimeError("work item implementation failed")) is None


def test_failed_forced_rerun_remains_forced_across_checkpoint():
    item = WorkItem(id="wi-owner", agent_id="code_agent", objective="repair", output_key="code")
    plan = ExecutionPlan(id="plan", goal="repair", work_items=(item,))
    state = RunState(plan, forced_rerun_work_item_ids={item.id},
                     recovery_diagnostics={item.id: "owner file invalid"})
    state.record(item, NodeResult.failed(work_item_id=item.id, agent_id=item.agent_id, error="retry failed"))
    checkpoint = state.as_checkpoint()
    assert checkpoint["forced_rerun_work_item_ids"] == [item.id]
    restored = RunState.from_checkpoint(plan, checkpoint)
    assert restored.forced_rerun_work_item_ids == {item.id}
    assert restored.recovery_diagnostics == {item.id: "owner file invalid"}
    restored.record(item, NodeResult.completed(work_item_id=item.id, agent_id=item.agent_id, content="fixed"))
    assert restored.forced_rerun_work_item_ids == set()
    assert restored.recovery_diagnostics == {}


def test_phase4_direct_runner_and_coordinator_matrix(tmp_path):
    result = run_validation(tmp_path)
    assert result["summary"] == {"passed": 7, "total": 7}
    by_case = {row["case"]: row for row in result["cases"]}
    assert by_case["codeagent-502"]["agent_calls"] == 2
    assert by_case["codeagent-503"]["agent_calls"] == 2
    assert by_case["no-changeset"]["agent_calls"] == 3
    for case in ("import-binding", "http-contract"):
        assert by_case[case]["executed_work_items"] == ["wi-integration", "wi-owner"]
        assert by_case[case]["completed_sibling_preserved"]
        assert by_case[case]["forced_rerun"] == ["wi-owner"]


def test_phase4_wave_conflict_and_runtime_start_failure(tmp_path):
    result = run_closure_validation(tmp_path)
    assert result["summary"] == {"passed": 2, "total": 2}
    wave, runtime = result["cases"]
    assert wave["downstream_not_started"] and wave["completed_sibling_preserved"]
    assert wave["forced_rerun"] == ["wi-owner"]
    assert runtime["static_preflight_passed"] and runtime["exit_code"] != 0
    assert runtime["failure_kind"] == FailureKind.SANDBOX_SETUP.value
    assert runtime["review_not_started"]

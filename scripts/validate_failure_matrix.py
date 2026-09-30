"""Deterministic failure-injection gate for the ProjectOS delivery control plane.

This does not claim that a real upstream service failed.  It injects normalized
signals at each boundary and verifies the control-plane contract: classification,
retry decision, evidence package, and bounded recovery scope.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestration.retry import (
    FailureKind,
    FailurePackage,
    FailureSignal,
    RecoveryAction,
    RetryPolicy,
)


CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "provider-403-policy",
        "kind": FailureKind.PROVIDER_POLICY_REJECTED,
        "retryable": False,
        "code": "upstream_policy_rejected",
        "scope": "run",
        "expected_action": RecoveryAction.BLOCK,
        "recovery_entrypoint": "provider_decision",
        "owner_files": (),
    },
    {
        "id": "provider-sse-timeout",
        "kind": FailureKind.PROVIDER_TRANSPORT,
        "retryable": True,
        "code": "provider_timeout",
        "scope": "current_work_item",
        "expected_action": RecoveryAction.RETRY_ITEM,
        "recovery_entrypoint": "retry_same_work_item",
        "owner_files": (),
    },
    {
        "id": "provider-terminal-event-missing",
        "kind": FailureKind.PROVIDER_TERMINAL_MISSING,
        "retryable": True,
        "code": "terminal_event_missing",
        "scope": "current_work_item",
        "expected_action": RecoveryAction.RETRY_ITEM,
        "recovery_entrypoint": "retry_same_work_item",
        "owner_files": (),
    },
    {
        "id": "architecture-schema-mismatch",
        "kind": FailureKind.TOOL_CONTRACT_MISMATCH,
        "retryable": False,
        "code": "schema_mismatch",
        "scope": "control_plane",
        "expected_action": RecoveryAction.BLOCK,
        "recovery_entrypoint": "tool_contract_repair",
        "owner_files": ("app/domain/architecture/design_contract.py",),
    },
    {
        "id": "codeagent-no-changeset",
        "kind": FailureKind.CODE_DELIVERY_INCOMPLETE,
        "retryable": True,
        "code": "changeset_missing",
        "scope": "current_work_item",
        "expected_action": RecoveryAction.RETRY_ITEM,
        "recovery_entrypoint": "retry_codeagent_owned_file",
        "owner_files": ("backend/main.py",),
    },
    {
        "id": "wave-merge-conflict",
        "kind": FailureKind.ARTIFACT_COMMIT_FAILURE,
        "retryable": False,
        "code": "wave_merge_conflict",
        "scope": "wave",
        "expected_action": RecoveryAction.BLOCK,
        "recovery_entrypoint": "freeze_wave_and_repair_changesets",
        "owner_files": ("backend/main.py", "frontend/index.html"),
    },
    {
        "id": "runtime-startup-failure",
        "kind": FailureKind.RUNTIME_PREFLIGHT,
        "retryable": False,
        "code": "runtime_start_failed",
        "scope": "run",
        "expected_action": RecoveryAction.BLOCK,
        "recovery_entrypoint": "runtime_repair",
        "owner_files": ("backend/main.py",),
    },
)


def run_matrix(output_path: str | Path | None = None) -> dict[str, Any]:
    policy = RetryPolicy()
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(CASES, start=1):
        work_item_id = f"wi-matrix-{case['id']}"
        evidence_id = f"ev-matrix-{index:02d}"
        signal = FailureSignal(
            case["kind"],
            f"injected {case['id']}",
            evidence_id=evidence_id,
            validator="FailureMatrix",
            code=case["code"],
            retryable=case["retryable"],
            scope=case["scope"],
            requires_control_plane=not case["retryable"],
            related_work_item_ids=(work_item_id,),
        )
        package = FailurePackage(
            signal=signal,
            check_id=f"check-{case['id']}",
            exit_code=1,
            stdout_excerpt="injected stdout",
            stderr_excerpt="injected stderr",
            repair_paths=case["owner_files"],
            owner_files=case["owner_files"],
            repair_scope=(work_item_id,),
        )
        action = policy.action_for(
            signal, total_retries=0, item_retries=0, kind_retries=0
        )
        # A retry is intentionally scoped to the failed item.  A control-plane
        # block has no automatic sibling replay at all.
        rerun_ids = (work_item_id,) if action is RecoveryAction.RETRY_ITEM else ()
        row = {
            "id": case["id"],
            "failure_kind": signal.kind.value,
            "retryable": signal.retryable,
            "scope": signal.scope,
            "code": signal.code,
            "evidence_id": signal.evidence_id,
            "failure_fingerprint": signal.failure_fingerprint,
            "expected_action": case["expected_action"].value,
            "actual_action": action.value,
            "failure_package": package.as_task_data(),
            "recovery_entrypoint": case["recovery_entrypoint"],
            "completed_siblings": ["wi-sibling-completed"],
            "rerun_work_item_ids": list(rerun_ids),
            "completed_siblings_replayed": "wi-sibling-completed" in rerun_ids,
            "passed": action is case["expected_action"] and not ("wi-sibling-completed" in rerun_ids),
        }
        if not row["passed"]:
            raise AssertionError(json.dumps(row, ensure_ascii=False))
        rows.append(row)

    result = {
        "schema_version": 1,
        "status": "passed",
        "policy": "RetryPolicy + FailurePackage",
        "cases": rows,
        "summary": {
            "total": len(rows),
            "passed": sum(row["passed"] for row in rows),
            "sibling_replay_count": sum(row["completed_siblings_replayed"] for row in rows),
        },
    }
    if output_path is not None:
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["evidence_path"] = str(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = run_matrix(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

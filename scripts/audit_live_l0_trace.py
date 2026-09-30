"""Read-only post-run audit of a real Live L0 trace; never starts a provider call.

Keep the launcher's original failure report untouched if its verifier failed
*after* the GraphRunner completed. This audit independently checks the Trace,
checkpoint, publication, and contract compiler, and writes separate evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.architecture.implementation_contract import ProjectContractStore
from app.orchestration.trace import TraceStore
from app.workflow.compiler import ImplementationContractCompiler
from scripts.validate_wanfa_architecture import audit_architecture_publication


def audit(project: Path, trace_id: str) -> dict[str, object]:
    project = project.resolve()
    traces = TraceStore(str(project))
    trace = traces.load_trace(trace_id)
    plan = traces.load_plan(trace_id)
    checkpoint = traces.load_checkpoint(trace_id)
    events = traces.list_events(trace_id)
    if trace["status"] != "completed":
        raise RuntimeError(f"Live L0 Trace is not completed: {trace['status']}")
    selection = trace.get("llm_selection") or {}
    if (selection.get("provider"), selection.get("model"), selection.get("wire_api")) != (
        "wanfa", "gpt-6-sol", "responses"
    ):
        raise RuntimeError(f"Live L0 provider selection mismatch: {selection}")
    if plan.template_id != "architecture_l0":
        raise RuntimeError(f"Unexpected L0 workflow: {plan.template_id}")
    checkpoint_results = checkpoint["node_results"]
    if {result["work_item_id"] for result in checkpoint_results} != {item.id for item in plan.work_items} or any(
        result["status"] != "completed" for result in checkpoint_results
    ):
        raise RuntimeError("L0 checkpoint does not cover all completed WorkItems")
    if any(item.agent_id in {"code_agent", "test_agent", "review_agent"} for item in plan.work_items):
        raise RuntimeError("L0 plan contains out-of-scope delivery WorkItems")
    publication = audit_architecture_publication(project, trace_id)
    contract = ProjectContractStore(str(project)).load()
    compiled = ImplementationContractCompiler().compile(
        contract, goal="Live L0 contract compile audit", plan_id="audit-live-l0", trace=plan.trace
    )
    if not compiled.work_items:
        raise RuntimeError("Contract cannot compile any implementation WorkItem")
    # A syntactically valid Contract is not yet a usable L0 handoff. These
    # fields are required for a real backend/frontend/runtime slice and may
    # currently be omitted by the Blueprint schema's optional defaults.
    from app.domain.architecture.handoff import contract_handoff_gaps
    gaps = contract_handoff_gaps(contract)
    for interface in contract.interfaces:
        if interface.kind == "api" and interface.owner_file and interface.owner_file.startswith("backend/") and not interface.operations:
            gaps.append("unfrozen API operations: " + interface.interface_id)
    root = traces.trace_root(trace_id)
    def file_info(path: Path) -> dict[str, str]:
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {
        "schema_version": 1, "status": "blocked_f_contract" if gaps else "verified_live_l0", "scope": "Live L0 only; no Live L1/Code execution",
        "contract_handoff_gaps": gaps,
        "project_path": str(project), "trace_id": trace_id,
        "provider": {key: selection[key] for key in ("provider", "model", "wire_api")},
        "trace_status": trace["status"], "workflow": plan.template_id,
        "planned_work_items": len(plan.work_items), "completed_work_items": len(checkpoint_results),
        "work_item_ids": [item.id for item in plan.work_items],
        "provider_retry_events": [event["work_item_id"] for event in events if event["type"] == "work_item_retrying"],
        "contract_units": len(contract.units), "compiled_code_work_items_not_run": len(compiled.work_items),
        "publication": publication,
        "raw": {key: file_info(path) for key, path in {
            "trace": root / "trace.json", "plan": root / "plan.json",
            "checkpoint": root / "checkpoint.json", "events": root / "events.jsonl",
            "requirement": project / "requirement.md", "architecture": project / "architecture.md",
            "contract": project / ".projectos/architecture/project-contract.json",
            "original_launcher_report": project / "validation-evidence.json",
        }.items()},
        "historical_verifier_exception_retained": True,
        "metrics": traces.metrics(trace_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.project, args.trace_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "trace_id", "planned_work_items", "completed_work_items", "contract_units", "compiled_code_work_items_not_run")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

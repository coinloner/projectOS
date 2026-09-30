"""Create a provenance-bound, inactive contract correction for one completed Live L0.

Never rewrites the canonical contract, published architecture, trace, plan or
checkpoint. The proposed runtime command is a design amendment, not an
observation that a provider emitted or that the command has run successfully.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.domain.architecture.design_contract import ArchitectureBlueprint, parse_design
from app.domain.architecture.handoff import contract_handoff_gaps
from app.domain.architecture.implementation_contract import ImplementationContract
from app.orchestration.trace import TraceContext
from app.workflow.compiler import ImplementationContractCompiler


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(project: Path, trace_id: str, candidate_path: Path, evidence_path: Path) -> dict:
    project = project.resolve()
    root = project / ".projectos/runs" / trace_id
    paths = {
        "contract": project / ".projectos/architecture/project-contract.json",
        "trace": root / "trace.json", "plan": root / "plan.json",
        "checkpoint": root / "checkpoint.json", "events": root / "events.jsonl",
        "architecture": project / "architecture.md", "requirement": project / "requirement.md",
    }
    before = {name: sha(path) for name, path in paths.items()}
    trace = json.loads(paths["trace"].read_text())
    selection = trace.get("llm_selection") or {}
    if trace.get("status") != "completed" or any(
        selection.get(key) != expected for key, expected in {
            "provider": "wanfa", "model": "gpt-6-sol", "wire_api": "responses"
        }.items()
    ):
        raise ValueError("Expected completed wanfa/gpt-6-sol/responses Live L0 Trace")
    plan = json.loads(paths["plan"].read_text())
    if plan.get("template_id") != "architecture_l0":
        raise ValueError("Only architecture_l0 is eligible for this revision")
    repository = ArtifactRepository(str(project))
    bp_ref = ArtifactRef.staged(artifact_key="architecture", trace_id=trace_id,
                                work_item_id="wi-02-architecture-blueprint", slot="blueprint")
    blueprint = parse_design(repository.load_ref(bp_ref))
    if not isinstance(blueprint, ArchitectureBlueprint) or blueprint.architecture_scheme != "D":
        raise ValueError("Expected staged D Blueprint from this Trace")
    raw = json.loads(paths["contract"].read_text())
    original = ImplementationContract.parse(raw)
    old_gaps = contract_handoff_gaps(original)
    if not old_gaps or raw["entrypoints"].get("backend_file") or raw["required_files"]:
        raise ValueError("Canonical contract differs from the expected uncorrected L0 baseline")
    owned = {path: unit for unit in raw["implementation_units"] for path in unit["owned_files"]}
    backend_file, frontend_file = "backend/http_service.py", "frontend/index.html"
    if backend_file not in owned or frontend_file not in owned:
        raise ValueError("Cannot find both runtime entry files in this Trace's owned files")
    if "app" not in owned[backend_file].get("provided_symbols", []):
        raise ValueError("Cannot identify a frozen ASGI app symbol on the backend owner")
    if not any(m.module_id == "http_service" and m.boundary_role == "runtime" for m in blueprint.modules):
        raise ValueError("Backend runtime role not present in source Blueprint")
    if not any(m.module_id == "browser_task_experience" and m.boundary_role == "user_experience" for m in blueprint.modules):
        raise ValueError("Frontend UX role not present in source Blueprint")
    proposed = copy.deepcopy(raw)
    proposed["entrypoints"] = {
        "backend_file": backend_file, "backend_import": "backend.http_service:app",
        "backend_command": "python -m uvicorn backend.http_service:app --host 0.0.0.0 --port 8000",
        "frontend_file": frontend_file, "health_path": original.entrypoints.health_path,
    }
    # Make all originally owned outputs mandatory; do not introduce a new
    # module, path, route, requirement or unowned file in a contract-only edit.
    proposed["required_files"] = sorted(owned)
    for unit in proposed["implementation_units"]:
        unit["required_paths"] = list(unit["owned_files"])
    candidate = ImplementationContract.parse(proposed)
    gaps = contract_handoff_gaps(candidate)
    if gaps:
        raise ValueError("Proposed contract fails handoff: " + "; ".join(gaps))
    compiled = ImplementationContractCompiler().compile(
        candidate, goal="Inactive L0 contract candidate dry run", plan_id="l0-f-candidate-dry-run",
        trace=TraceContext.ephemeral(),
    )
    if len(compiled.work_items) != len(candidate.units):
        raise ValueError("Candidate code compilation did not preserve all units")
    if candidate_path.exists() or evidence_path.exists():
        raise FileExistsError("Candidate/evidence already exists; refusing to overwrite an audit artifact")
    after = {name: sha(path) for name, path in paths.items()}
    if after != before:
        raise RuntimeError("One or more original authoritative inputs changed during candidate construction")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(proposed, ensure_ascii=False, indent=2) + "\n")
    evidence = {
        "schema_version": 1, "status": "candidate_validated_not_activated",
        "classification": {"contract_revision": "F", "handoff_gate": "R", "evidence": "V"},
        "trace_id": trace_id, "trace_status": trace["status"],
        "source_blueprint_ref": bp_ref.ref_id,
        "source_blueprint_digest": hashlib.sha256(repository.load_ref(bp_ref).encode()).hexdigest(),
        "source_artifact_sha256": {name: {"path": str(paths[name]), "sha256": value}
                                   for name, value in before.items()},
        "original_contract_digest": digest(raw), "original_handoff_gaps": old_gaps,
        "candidate_path": str(candidate_path.resolve()), "candidate_file_sha256": sha(candidate_path),
        "candidate_digest": digest(proposed), "candidate_handoff_gaps": gaps,
        "required_files": proposed["required_files"],
        "compiled_code_work_items_not_run": len(compiled.work_items),
        "changes": ["entrypoints", "required_files", "implementation_units[].required_paths"],
        "decision_basis": {
            "backend_file": "source unit http_service.api owns backend/http_service.py; FastAPI entry objective",
            "backend_symbol": "source unit http_service.api declares provided_symbols=['app']",
            "frontend_file": "source unit browser_task_experience.frontend_runtime owns frontend/index.html",
            "command": "proposed uvicorn command from source FastAPI runtime profile and owned app symbol; not executed",
            "required_files": "all 11 existing owned files, with no new unit/path",
        },
        "limitations": [
            "Candidate has not been published or activated; canonical Trace remains unchanged and Live L0 gate remains blocked",
            "Startup command is a proposed engineering decision, not provider output or runtime proof",
            "Source requirement AC-10 startup/access instructions have no dedicated owned documentation file; needs explicit design revision before final delivery",
            "No Provider, CodeAgent, Runtime, API or browser execution in this slice",
        ],
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    if {name: sha(path) for name, path in paths.items()} != before:
        raise RuntimeError("Original artifact changed after writing candidate")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.project, args.trace_id, args.candidate, args.evidence)
    print(json.dumps({key: result[key] for key in ("status", "trace_id", "candidate_digest",
                                                   "compiled_code_work_items_not_run")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

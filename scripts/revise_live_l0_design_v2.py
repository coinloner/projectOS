"""Derive inactive Blueprint + Contract corrections from one real Live L0.

The canonical publication and checkpoint are immutable. Both versioned copies
must be explicitly migrated later; generating them is not Live L0 acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.domain.architecture.contract_input import ProjectContractInput
from app.domain.architecture.design_contract import ArchitectureBlueprint, ArchitectureDesignBundle, parse_design
from app.domain.architecture.handoff import blueprint_handoff_gaps, bundle_handoff_gaps, contract_handoff_gaps
from app.domain.architecture.implementation_contract import ImplementationContract
from app.orchestration.trace import TraceContext
from app.workflow.compiler import ImplementationContractCompiler
from scripts.revise_live_l0_contract import digest, sha

MODULE_IDS = ("task_management", "http_service", "browser_task_experience")
TRACE = "tr-b23fd9b89e41"


def run(project: Path, blueprint_candidate: Path, contract_candidate: Path, evidence_path: Path) -> dict:
    project = project.resolve()
    paths = {
        "contract": project / ".projectos/architecture/project-contract.json",
        "trace": project / f".projectos/runs/{TRACE}/trace.json",
        "plan": project / f".projectos/runs/{TRACE}/plan.json",
        "checkpoint": project / f".projectos/runs/{TRACE}/checkpoint.json",
        "events": project / f".projectos/runs/{TRACE}/events.jsonl",
        "architecture": project / "architecture.md", "requirement": project / "requirement.md",
    }
    if any(path.exists() for path in (blueprint_candidate, contract_candidate, evidence_path)):
        raise FileExistsError("v2 candidate already exists; do not overwrite provenance or evidence")
    before = {name: sha(path) for name, path in paths.items()}
    trace = json.loads(paths["trace"].read_text())
    selection = trace.get("llm_selection") or {}
    if trace.get("status") != "completed" or any(selection.get(k) != v for k, v in {
        "provider": "wanfa", "model": "gpt-6-sol", "wire_api": "responses"
    }.items()):
        raise ValueError("Source must be the completed wanfa/gpt-6-sol/responses L0")
    if json.loads(paths["plan"].read_text()).get("template_id") != "architecture_l0":
        raise ValueError("Source plan is not Live L0")
    repository = ArtifactRepository(str(project))
    def load(work_item_id: str, slot: str):
        ref = ArtifactRef.staged(artifact_key="architecture", trace_id=TRACE,
                                 work_item_id=work_item_id, slot=slot)
        content = repository.load_ref(ref)
        return parse_design(content), {"ref": ref.ref_id, "digest": __import__("hashlib").sha256(content.encode()).hexdigest()}
    old_blueprint, bp_source = load("wi-02-architecture-blueprint", "blueprint")
    if not isinstance(old_blueprint, ArchitectureBlueprint) or old_blueprint.architecture_scheme != "D":
        raise ValueError("Source must be an actual D Blueprint")
    modules, module_sources, implementations, implementation_sources = [], [], [], []
    for module_id in MODULE_IDS:
        design, source = load("wi-architecture-module-" + module_id, "module-" + module_id)
        modules.append(design); module_sources.append(source)
        design, source = load("wi-architecture-implementation-" + module_id, "implementation-" + module_id)
        implementations.append(design); implementation_sources.append(source)
    original_raw = json.loads(paths["contract"].read_text())
    original = ImplementationContract.parse(original_raw)
    if not contract_handoff_gaps(original):
        raise ValueError("Source contract is not the known blocked handoff")
    owned = sorted({path for design in implementations for unit in design.implementation_units
                    for path in unit.owned_files})
    for path in ("backend/http_service.py", "frontend/index.html"):
        if path not in owned:
            raise ValueError(f"Source implementation does not own proposed entrypoint {path}")
    blueprint_data = old_blueprint.model_dump(mode="json")
    blueprint_data["entrypoints"] = {
        "backend_file": "backend/http_service.py", "backend_import": "backend.http_service:app",
        "backend_command": "python -m uvicorn backend.http_service:app --host 0.0.0.0 --port 8000",
        "frontend_file": "frontend/index.html", "health_path": "/health",
    }
    blueprint_data["required_files"] = owned
    for module in blueprint_data["modules"]:
        module["owned_required_files"] = sorted({path for design in implementations
            if design.module_id == module["module_id"] for unit in design.implementation_units
            for path in unit.owned_files})
    blueprint = ArchitectureBlueprint.model_validate(blueprint_data)
    if blueprint_handoff_gaps(blueprint):
        raise ValueError("Revised Blueprint failed producer gate: " + "; ".join(blueprint_handoff_gaps(blueprint)))
    bundle = ArchitectureDesignBundle(schema_version=1, blueprint=blueprint,
                                      modules=modules, implementations=implementations)
    if bundle_handoff_gaps(bundle):
        raise ValueError("Revised design failed integrated gate: " + "; ".join(bundle_handoff_gaps(bundle)))
    contract = ImplementationContract.parse(
        ProjectContractInput.model_validate(bundle.to_project_contract()).to_canonical_dict()
    )
    candidate_data = contract.as_dict()
    diff_keys = [key for key, value in candidate_data.items() if value != original.as_dict()[key]]
    if set(diff_keys) != {"entrypoints", "required_files"}:
        raise ValueError(f"Derived contract unexpectedly changed non-handoff fields: {diff_keys}")
    if contract_handoff_gaps(contract):
        raise ValueError("Derived candidate failed contract gate: " + "; ".join(contract_handoff_gaps(contract)))
    compiled = ImplementationContractCompiler().compile(contract, goal="Inactive L0 design correction dry run",
        plan_id="l0-design-v2-dry-run", trace=TraceContext.ephemeral())
    if len(compiled.work_items) != 8:
        raise ValueError(f"Expected 8 original units, got {len(compiled.work_items)}")
    if {name: sha(path) for name, path in paths.items()} != before:
        raise RuntimeError("Source changed while constructing v2")
    for path in (blueprint_candidate, contract_candidate, evidence_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_candidate.write_text(json.dumps(blueprint_data, ensure_ascii=False, indent=2) + "\n")
    contract_candidate.write_text(json.dumps(candidate_data, ensure_ascii=False, indent=2) + "\n")
    evidence = {
        "schema_version": 1, "status": "design_and_contract_candidates_validated_not_activated",
        "trace_id": TRACE, "source_trace_status": "completed", "classification": {"design": "F", "gate": "R", "audit": "V"},
        "source_artifact_sha256": {name: {"path": str(paths[name]), "sha256": value}
                                   for name, value in before.items()},
        "source_blueprint": bp_source, "source_modules": module_sources,
        "source_implementations": implementation_sources,
        "blueprint_candidate": {"path": str(blueprint_candidate.resolve()), "sha256": sha(blueprint_candidate)},
        "contract_candidate": {"path": str(contract_candidate.resolve()), "sha256": sha(contract_candidate),
                               "digest": digest(candidate_data)},
        "original_contract_digest": digest(original_raw),
        "only_contract_fields_changed": diff_keys,
        "required_files": owned, "blueprint_handoff_gaps": [], "bundle_handoff_gaps": [],
        "contract_handoff_gaps": [], "compiled_code_work_items_not_run": len(compiled.work_items),
        "supersedes": "live-l0-f-handoff-v1-20260929.json (proposed required_paths changes not needed for source-consistent derivation)",
        "decision_basis": {
            "backend_entry": "http_service.api owns backend/http_service.py and declares provided symbol app",
            "frontend_entry": "browser_task_experience.frontend_runtime owns frontend/index.html",
            "command": "proposed ASGI startup based on FastAPI runtime profile; not emitted by Provider or executed",
            "required_files": "all 11 source-owned implementation files assigned to the original three modules",
        },
        "limitations": [
            "Not active: no published Blueprint/Architecture revision, contract replacement, checkpoint replan or Live L1",
            "Proposed uvicorn cwd/dependencies/command have not passed a live runtime check",
            "AC-10 startup/access instructions still have no owned documentation file in source implementation design",
            "Original failed Live Trace tr-2e4924e985cf remains separately unresolved",
        ],
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    if {name: sha(path) for name, path in paths.items()} != before:
        raise RuntimeError("Source changed after writing v2")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.project, args.blueprint, args.contract, args.evidence)
    print(json.dumps({"status": result["status"], "trace_id": result["trace_id"],
                      "contract_digest": result["contract_candidate"]["digest"],
                      "compiled_code_work_items_not_run": result["compiled_code_work_items_not_run"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

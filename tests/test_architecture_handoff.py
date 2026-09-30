"""New D writes fail closed; old contracts remain inspectable for repair."""
from __future__ import annotations

import json

import pytest

from app.domain.architecture.contract_input import ContractEntrypointInput
from app.domain.architecture.design_contract import ArchitectureBlueprint, ArchitectureDesignBundle, ImplementationDesign, ModuleDesign
from app.domain.architecture.handoff import blueprint_handoff_gaps, bundle_handoff_gaps, contract_handoff_gaps
from app.domain.architecture.implementation_contract import ImplementationContract


def _blueprint(**overrides):
    raw = {
        "schema_version": 1, "architecture_scheme": "D", "design_id": "bp", "depth": 0,
        "system_boundary": "任务服务与浏览器", "layers": [
            {"name": "backend", "allowed_dependencies": [], "path_mapping": ["backend/**"]},
            {"name": "frontend", "allowed_dependencies": ["backend"], "path_mapping": ["frontend/**"]},
        ], "modules": [
            {"module_id": "api", "boundary_role": "runtime", "responsibility": "HTTP", "owned_required_files": ["backend/http_service.py"]},
            {"module_id": "browser", "boundary_role": "user_experience", "responsibility": "页面", "owned_required_files": ["frontend/index.html"]},
        ],
        "entrypoints": {"backend_file": "backend/http_service.py", "backend_import": "backend.http_service:app",
                        "backend_command": "python -m uvicorn backend.http_service:app", "frontend_file": "frontend/index.html"},
        "required_files": ["backend/http_service.py", "frontend/index.html"],
    }
    raw.update(overrides)
    return ArchitectureBlueprint.model_validate(raw)


def _bundle(bp):
    return ArchitectureDesignBundle(
        schema_version=1, blueprint=bp,
        modules=[ModuleDesign(schema_version=1, design_id=f"m-{i}", parent_design_id="bp",
                              module_id=i, responsibilities=[i]) for i in ("api", "browser")],
        implementations=[
            ImplementationDesign(schema_version=1, design_id="i-api", parent_design_id="m-api",
                module_id="api", implementation_units=[{
                    "unit_id": "api", "layer": "backend", "objective": "FastAPI entry",
                    "allowed_paths": ["backend/**"], "owned_files": ["backend/http_service.py"],
                    "provided_symbols": ["app"],
                }]),
            ImplementationDesign(schema_version=1, design_id="i-browser", parent_design_id="m-browser",
                module_id="browser", implementation_units=[{
                    "unit_id": "browser", "layer": "frontend", "objective": "HTML entry",
                    "allowed_paths": ["frontend/**"], "owned_files": ["frontend/index.html"],
                }]),
        ],
    )


def test_new_d_blueprint_requires_executable_handoff():
    bp = _blueprint().model_copy(update={"entrypoints": ContractEntrypointInput(), "required_files": []})
    gaps = blueprint_handoff_gaps(bp)
    assert "entrypoints.backend_file missing" in gaps
    assert "entrypoints.frontend_file missing" in gaps
    assert "required_files empty" in gaps
    assert blueprint_handoff_gaps(_blueprint()) == []


def test_blueprint_rejects_wrong_command_and_module_assignment():
    bp = _blueprint(entrypoints={
        "backend_file": "backend/http_service.py", "backend_import": "backend.other:app",
        "backend_command": "python -m uvicorn backend.unrelated:app",
        "frontend_file": "frontend/index.html",
    })
    gaps = blueprint_handoff_gaps(bp)
    assert any("does not match backend_file" in item for item in gaps)
    assert any("does not launch backend_import" in item for item in gaps)
    bp = _blueprint(modules=[
        {"module_id": "api", "boundary_role": "runtime", "responsibility": "HTTP", "owned_required_files": []},
        {"module_id": "browser", "boundary_role": "user_experience", "responsibility": "页面", "owned_required_files": ["frontend/index.html", "backend/http_service.py"]},
    ])
    assert any("not assigned to runtime" in item for item in blueprint_handoff_gaps(bp))


def test_bundle_checks_entrypoint_symbol_and_owned_files():
    assert bundle_handoff_gaps(_bundle(_blueprint())) == []
    bp = _blueprint(entrypoints={
        "backend_file": "backend/http_service.py", "backend_import": "backend.http_service:other",
        "backend_command": "python -m uvicorn backend.http_service:other",
        "frontend_file": "frontend/index.html",
    })
    assert "entrypoints.backend_import symbol not provided: other" in bundle_handoff_gaps(_bundle(bp))


def test_historical_contract_loads_but_fails_new_handoff_policy():
    bundle = _bundle(_blueprint())
    from app.domain.architecture.contract_input import ProjectContractInput
    contract = ImplementationContract.parse(
        ProjectContractInput.model_validate(bundle.to_project_contract()).to_canonical_dict()
    )
    assert contract_handoff_gaps(contract) == []
    raw = json.loads(json.dumps(contract.as_dict()))
    raw["entrypoints"] = {"backend_file": None, "backend_import": None,
                          "backend_command": None, "frontend_file": None, "health_path": "/health"}
    raw["required_files"] = []
    old = ImplementationContract.parse(raw)
    assert "required_files empty" in contract_handoff_gaps(old)


def test_new_semantic_contract_save_fails_closed_without_overwriting(tmp_path):
    from app.domain.architecture.contract_input import ProjectContractInput
    from app.domain.architecture.service import ArchitectureService
    from app.domain.architecture.implementation_contract import ProjectContractStore

    wire = _bundle(_blueprint()).to_project_contract()
    service = ArchitectureService(str(tmp_path))
    valid = ProjectContractInput.model_validate(wire).to_canonical_dict()
    saved = service.save_implementation_contract(valid)
    assert "implementation" in saved.lower() or "contract" in saved.lower()
    canonical = (tmp_path / ProjectContractStore.relative_path)
    before = canonical.read_bytes()
    invalid = json.loads(json.dumps(valid))
    invalid["entrypoints"] = {"health_path": "/health"}
    invalid["required_files"] = []
    with pytest.raises(ValueError, match="project_contract_handoff_incomplete"):
        service.save_implementation_contract(invalid)
    assert canonical.read_bytes() == before


def test_recursive_blueprint_writer_rejects_empty_handoff_before_staging(tmp_path):
    from app.architecture_execution_config import ArchitectureExecutionConfig
    from app.artifact.repository import ArtifactRef, ArtifactRepository
    from app.domain.architecture.service import ArchitectureArtifactWorkflow
    from app.execution_context import ExecutionContext, ExecutionMode

    context = ExecutionContext(
        trace_id="tr-empty-handoff", work_item_id="wi-blueprint", agent_id="architecture_agent",
        execution_mode=ExecutionMode.PARTITIONED, slot="blueprint",
        architecture_config=ArchitectureExecutionConfig(scheme="D"),
    )
    broken = _blueprint().model_dump(mode="json")
    broken["entrypoints"] = {"health_path": "/health"}
    broken["required_files"] = []
    for module in broken["modules"]:
        module["owned_required_files"] = []
    workflow = ArchitectureArtifactWorkflow(str(tmp_path))
    with pytest.raises(ValueError, match="blueprint_handoff_incomplete"):
        workflow.write_staged_design(context, broken)
    ref = ArtifactRef.staged(artifact_key="architecture", trace_id=context.trace_id,
                             work_item_id=context.work_item_id, slot="blueprint")
    with pytest.raises((FileNotFoundError, ValueError)):
        ArtifactRepository(str(tmp_path)).load_ref(ref)

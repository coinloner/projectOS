from app.domain.architecture.contract_input import CanonicalContractNormalizer
from app.domain.architecture.service import ArchitectureService


def _draft():
    return {
        "schema_version": 1,
        "strategy": "file",
        "layers": {
            "runtime": {"dependencies": [], "paths": ["backend/**"]},
        },
        "requiredTests": ["unit"],
        "entrypoint": {"backend_file": "backend/main.py", "backend_import": "backend.main"},
        "requiredPaths": ["backend/main.py"],
        "interfaces": [],
        "units": [{
            "unit_id": "backend",
            "layer": "runtime",
            "objective": "run",
            "allowed_paths": ["backend/**"],
            "required_files": ["backend/main.py"],
            "owned_files": ["backend/main.py"],
        }],
    }


def test_normalizer_emits_only_canonical_wire_names():
    normalized = CanonicalContractNormalizer.normalize(_draft())
    assert "entrypoint" not in normalized
    assert "implementation_units" in normalized
    assert normalized["implementation_units"][0]["required_paths"] == ["backend/main.py"]
    assert normalized["layers"][0]["path_mapping"] == ["backend/**"]
    assert "dependencies" not in normalized["layers"][0]


def test_architecture_service_persists_canonical_contract(tmp_path):
    ArchitectureService(str(tmp_path)).save_implementation_contract(_draft())
    payload = (tmp_path / ".projectos/architecture/project-contract.json").read_text()
    assert "required_files" in payload
    assert "required_paths" in payload
    assert "\"entrypoint\"" not in payload
    assert "entrypoints" in payload


def test_new_project_contract_requires_frozen_api_operations(tmp_path):
    draft = _draft()
    draft["interfaces"] = [{
        "interface_id": "todo.api", "kind": "api", "name": "Todo API",
        "owner_unit": "backend", "owner_file": "backend/main.py",
    }]
    import pytest
    with pytest.raises(ValueError, match="必须冻结 operations"):
        ArchitectureService(str(tmp_path)).save_implementation_contract(draft)


def test_new_project_contract_accepts_frozen_api_operations(tmp_path):
    draft = _draft()
    draft["interfaces"] = [{
        "interface_id": "todo.api", "kind": "api", "name": "Todo API",
        "owner_unit": "backend", "owner_file": "backend/main.py",
        "operations": [{"method": "GET", "path": "/tasks"}],
    }]
    ArchitectureService(str(tmp_path)).save_implementation_contract(draft)

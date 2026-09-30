from app.orchestration.retry import FailureKind, RecoveryAction
from scripts.validate_failure_matrix import run_matrix


def test_failure_matrix_classifies_all_boundaries_without_replaying_siblings():
    result = run_matrix()
    assert result["status"] == "passed"
    assert result["summary"] == {"total": 7, "passed": 7, "sibling_replay_count": 0}
    kinds = {row["failure_kind"] for row in result["cases"]}
    assert FailureKind.PROVIDER_POLICY_REJECTED.value in kinds
    assert FailureKind.ARTIFACT_COMMIT_FAILURE.value in kinds
    assert all(row["failure_package"]["signal"]["evidence_id"] for row in result["cases"])


def test_failure_matrix_persists_json_evidence(tmp_path):
    result = run_matrix(tmp_path / "failure-matrix.json")
    assert result["evidence_path"].endswith("failure-matrix.json")
    assert (tmp_path / "failure-matrix.json").is_file()

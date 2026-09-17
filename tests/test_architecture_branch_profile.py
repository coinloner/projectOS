import json
import subprocess

import pytest

from scripts.architecture_branch_profile import MODES, source_identity, validate_branch_profile


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("checkout", "-qb", "codex/test-a")
    git("commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD")
    def profile(arm="A", status="implemented"):
        data = dict(schema_version=1, arm=arm, mode=MODES[arm], branch="codex/test-a",
                    common_snapshot=base, implementation_status=status)
        (tmp_path / "architecture-experiment.json").write_text(json.dumps(data))
        return data
    return tmp_path, git, profile


def test_legacy_ab_allowed_but_cd_never_falls_back(tmp_path):
    assert validate_branch_profile(tmp_path, "A") is None
    assert validate_branch_profile(tmp_path, "B") is None
    for arm in ("C", "D"):
        with pytest.raises(ValueError, match="no implemented execution path"):
            validate_branch_profile(tmp_path, arm)


def test_arm_and_actual_branch_must_match(repo):
    root, git, profile = repo
    expected = profile()
    assert validate_branch_profile(root, "A") == expected
    with pytest.raises(ValueError, match="differs"):
        validate_branch_profile(root, "B")
    git("checkout", "-qb", "codex/another")
    with pytest.raises(ValueError, match="Checkout branch"):
        validate_branch_profile(root, "A")


@pytest.mark.parametrize("arm", ["C", "D"])
def test_editing_readiness_label_cannot_enable_missing_implementation(repo, arm):
    root, _, profile = repo
    profile(arm, "implemented")
    with pytest.raises(ValueError, match="no implemented execution path"):
        validate_branch_profile(root, arm)


def test_incomplete_profile_rejected_even_with_explicit_supported_path(repo):
    root, _, profile = repo
    profile("C", "not_implemented")
    with pytest.raises(ValueError, match="incomplete"):
        validate_branch_profile(root, "C", supported_arms=("A", "B", "C"))


def test_git_identity_records_uncommitted_changes(repo):
    root, git, profile = repo
    assert source_identity(root)["dirty"] is False
    profile()
    evidence = source_identity(root)
    assert evidence["dirty"] is True
    assert evidence["commit"] == git("rev-parse", "HEAD")


def test_fourway_preflight_rejects_shared_worktree_before_loading_bundle(tmp_path):
    from scripts.check_architecture_fourway import inspect_manifest
    manifest = {"arms": {arm: {"worktree": str(tmp_path), "branch": f"codex/{arm}"}
                          for arm in MODES}}
    path = tmp_path / "branches.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="distinct worktree"):
        inspect_manifest(path)


def test_fourway_preflight_requires_all_arms(tmp_path):
    from scripts.check_architecture_fourway import inspect_manifest
    path = tmp_path / "branches.json"
    path.write_text(json.dumps({"arms": {"A": {}}}))
    with pytest.raises(ValueError, match="Exactly four"):
        inspect_manifest(path)

"""Fail-closed branch identity for isolated Architecture experiments.

An experiment profile is not a production runtime setting or readiness proof.
C/D remain rejected until their real execution paths replace the A/B-only runner.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

MODES = {"A": "baseline", "B": "checkpointed", "C": "parent_feedback", "D": "recursive"}


def validate_branch_profile(root: Path, arm: str, *, supported_arms=("A", "B")) -> dict | None:
    if arm not in MODES:
        raise ValueError(f"Unknown experiment arm: {arm}")
    profile_path = root / "architecture-experiment.json"
    if not profile_path.exists():
        # Keep the historical two-arm harness usable outside isolated branches.
        if arm not in supported_arms:
            raise ValueError(f"Scheme {arm} has no implemented execution path")
        return None
    profile = json.loads(profile_path.read_text())
    if profile.get("schema_version") != 1:
        raise ValueError("Unsupported branch profile schema")
    if profile.get("arm") != arm or profile.get("mode") != MODES[arm]:
        raise ValueError("Requested arm differs from isolated branch profile")
    branch = subprocess.check_output(
        ["git", "-C", str(root), "symbolic-ref", "--short", "HEAD"], text=True
    ).strip()
    if branch != profile.get("branch"):
        raise ValueError("Checkout branch differs from experiment profile")
    subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", profile["common_snapshot"], "HEAD"],
        check=True, capture_output=True,
    )
    if arm not in supported_arms:
        raise ValueError(f"Scheme {arm} has no implemented execution path; benchmark prohibited")
    if profile.get("implementation_status") != "implemented":
        raise ValueError(f"Scheme {arm} implementation is incomplete; benchmark prohibited")
    return profile


def source_identity(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    return {"worktree": str(root.resolve()), "commit": git("rev-parse", "HEAD"),
            "branch": git("symbolic-ref", "--short", "HEAD"),
            "dirty": bool(git("status", "--porcelain"))}

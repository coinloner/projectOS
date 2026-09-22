"""Read-only preflight. It never calls wanfa or treats a profile as success evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.architecture_branch_profile import MODES
from scripts.freeze_architecture_comparison import verify_bundle


def inspect_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text())
    arms = manifest["arms"]
    if set(arms) != set(MODES):
        raise ValueError("Exactly four arms A/B/C/D are required")
    paths = [str(Path(arms[a]["worktree"]).resolve()) for a in MODES]
    branches = [arms[a]["branch"] for a in MODES]
    if len(set(paths)) != 4 or len(set(branches)) != 4:
        raise ValueError("Each scheme must have a distinct worktree and branch")
    bundle = verify_bundle(Path(manifest["bundle"]))
    results = []
    for arm in MODES:
        root = Path(arms[arm]["worktree"])
        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
        record = {"arm": arm, "worktree": str(root), "blockers": []}
        try:
            record["commit"] = git("rev-parse", "HEAD")
            record["branch"] = git("symbolic-ref", "--short", "HEAD")
            if record["branch"] != arms[arm]["branch"]:
                record["blockers"].append("branch mismatch")
            if git("status", "--porcelain"):
                record["blockers"].append("uncommitted experiment code")
            subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                            manifest["common_snapshot"], "HEAD"], check=True, capture_output=True)
            profile = json.loads((root / "architecture-experiment.json").read_text())
            if (profile.get("arm") != arm or profile.get("mode") != MODES[arm]
                    or profile.get("branch") != record["branch"]
                    or profile.get("common_snapshot") != manifest["common_snapshot"]):
                record["blockers"].append("profile mismatch")
            if profile.get("implementation_status") != "implemented":
                record["blockers"].append("implementation incomplete")
            # This harness has no C/D executor. Labels must not unlock it.
            if arm in ("C", "D"):
                record["blockers"].append("closed-loop executor and wanfa readiness evidence not verified")
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
            record["blockers"].append(f"inspection failed: {error}")
        results.append(record)
    return {"scope": "four-branch setup preflight; not experiment results",
            "common_snapshot": manifest["common_snapshot"],
            "bundle_digest": bundle["bundle_digest"], "arms": results,
            "ready_to_benchmark": all(not a["blockers"] for a in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_manifest(args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_to_benchmark"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

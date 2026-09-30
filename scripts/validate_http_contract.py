"""Run the deterministic cross-unit HTTP contract gate for a generated project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.architecture.implementation_contract import ImplementationContractStore
from app.domain.code.http_contract import validate_http_consumers


def run(project: str | Path, output: str | Path | None = None) -> dict[str, object]:
    root = Path(project).resolve()
    contract = ImplementationContractStore(str(root)).load()
    issues = validate_http_consumers(root / "workspace", contract)
    evidence: dict[str, object] = {
        "schema_version": 1,
        "project_path": str(root),
        "validator": "http_contract_consumer_gate",
        "status": "passed" if not issues else "failed",
        "issue_count": len(issues),
        "issues": list(issues),
    }
    if output is not None:
        path = Path(output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence["evidence_path"] = str(path)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        evidence = run(args.project, args.output)
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

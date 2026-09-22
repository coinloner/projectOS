"""Deterministic projection of the accepted Project Contract into tasks.md."""
from __future__ import annotations

from app.artifact.store import ArtifactStore
from app.domain.architecture.implementation_contract import ImplementationContract


def render_tasks(contract: ImplementationContract) -> str:
    lines = [
        "# Implementation Tasks",
        "",
        "> This file is a deterministic projection of the accepted Project Contract.",
        "> The contract, not this document, is authoritative for execution.",
        "",
    ]
    for index, unit in enumerate(contract.units, start=1):
        lines.extend([
            f"## {index}. {unit.unit_id}",
            "",
            f"- Objective: {unit.objective}",
            f"- Layer: {unit.layer}",
            f"- Owned files: {', '.join(unit.owned_files)}",
            f"- Depends on: {', '.join(unit.depends_on) if unit.depends_on else 'none'}",
        ])
        if unit.acceptance_criteria:
            lines.append("- Acceptance:")
            lines.extend(f"  - {criterion}" for criterion in unit.acceptance_criteria)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def project_tasks(project_path: str, contract: ImplementationContract) -> str:
    content = render_tasks(contract)
    ArtifactStore(project_path).save("tasks", content)
    return content

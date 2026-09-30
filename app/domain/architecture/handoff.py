"""Fail-closed delivery handoff checks for newly written recursive designs.

Historical artifacts remain parseable; call these checks only at producer and
publication boundaries, not while loading an old checkpoint for diagnosis.
"""
from __future__ import annotations

import shlex

from app.domain.architecture.design_contract import ArchitectureBlueprint, ArchitectureDesignBundle
from app.domain.architecture.implementation_contract import ImplementationContract


def _backend_module(file: str) -> str:
    return file.removesuffix(".py").replace("/", ".")


def _entrypoint_gaps(
    *, backend_file: str | None, backend_import: str | None,
    backend_command: str | None, frontend_file: str | None,
    required_files: set[str], needs_backend: bool, needs_frontend: bool,
) -> list[str]:
    gaps: list[str] = []
    if not required_files:
        gaps.append("required_files empty")
    if needs_backend:
        for key, value in (("backend_file", backend_file), ("backend_import", backend_import),
                           ("backend_command", backend_command)):
            if not value:
                gaps.append(f"entrypoints.{key} missing")
    if needs_frontend and not frontend_file:
        gaps.append("entrypoints.frontend_file missing")
    for key, path in (("backend_file", backend_file), ("frontend_file", frontend_file)):
        if path and path not in required_files:
            gaps.append(f"entrypoints.{key} not in required_files: {path}")
    if backend_file and backend_import:
        module = backend_import.split(":", 1)[0]
        expected = _backend_module(backend_file)
        # A process may run from project root or backend/. Do not require one
        # cwd convention, but never accept a different source module.
        if not backend_file.endswith(".py") or module not in {
            expected, expected.removeprefix("backend.")
        }:
            gaps.append("entrypoints.backend_import does not match backend_file")
    if backend_import and backend_command:
        try:
            argv = shlex.split(backend_command)
        except ValueError:
            argv = []
        if backend_import not in argv:
            gaps.append("entrypoints.backend_command does not launch backend_import")
    return gaps


def blueprint_handoff_gaps(blueprint: ArchitectureBlueprint) -> list[str]:
    """Check a new D Blueprint while its producing Agent can still repair it."""
    if blueprint.architecture_scheme != "D":
        return []
    role_files: dict[str, set[str]] = {role: set() for role in ("runtime", "user_experience")}
    for module in blueprint.modules:
        if module.boundary_role in role_files:
            role_files[module.boundary_role].update(module.owned_required_files)
    ep = blueprint.entrypoints
    gaps = _entrypoint_gaps(
        backend_file=ep.backend_file, backend_import=ep.backend_import,
        backend_command=ep.backend_command, frontend_file=ep.frontend_file,
        required_files=set(blueprint.required_files),
        needs_backend=any(m.boundary_role == "runtime" for m in blueprint.modules),
        needs_frontend=any(m.boundary_role == "user_experience" for m in blueprint.modules),
    )
    for key, path, role in (("backend_file", ep.backend_file, "runtime"),
                            ("frontend_file", ep.frontend_file, "user_experience")):
        if path and path not in role_files[role]:
            gaps.append(f"entrypoints.{key} not assigned to {role} module: {path}")
    return gaps


def bundle_handoff_gaps(bundle: ArchitectureDesignBundle) -> list[str]:
    """Check the integrated D design before publishing its candidate."""
    gaps = blueprint_handoff_gaps(bundle.blueprint)
    if bundle.blueprint.architecture_scheme != "D":
        return gaps
    owned = {path: unit for design in bundle.implementations
             for unit in design.implementation_units for path in unit.owned_files}
    for key, path in (("backend_file", bundle.blueprint.entrypoints.backend_file),
                      ("frontend_file", bundle.blueprint.entrypoints.frontend_file)):
        if path and path not in owned:
            gaps.append(f"entrypoints.{key} not owned by implementation unit: {path}")
    ep = bundle.blueprint.entrypoints
    if ep.backend_file and ep.backend_import and ":" in ep.backend_import and ep.backend_file in owned:
        symbol = ep.backend_import.split(":", 1)[1]
        if symbol not in owned[ep.backend_file].provided_symbols:
            gaps.append(f"entrypoints.backend_import symbol not provided: {symbol}")
    return gaps


def contract_handoff_gaps(contract: ImplementationContract) -> list[str]:
    """Check a versioned candidate before it can be proposed for activation."""
    owned = {path: unit for unit in contract.units for path in unit.owned_files}
    ep = contract.entrypoints
    gaps = _entrypoint_gaps(
        backend_file=ep.backend_file, backend_import=ep.backend_import,
        backend_command=ep.backend_command, frontend_file=ep.frontend_file,
        required_files=set(contract.required_files),
        needs_backend=any(path.startswith("backend/") for path in owned),
        needs_frontend=any(path.startswith("frontend/") for path in owned),
    )
    for key, path in (("backend_file", ep.backend_file), ("frontend_file", ep.frontend_file)):
        if path and path not in owned:
            gaps.append(f"entrypoints.{key} not owned by implementation unit: {path}")
    for path in contract.required_files:
        if path not in owned:
            gaps.append(f"required_files not owned by implementation unit: {path}")
    if ep.backend_file and ep.backend_import and ":" in ep.backend_import and ep.backend_file in owned:
        symbol = ep.backend_import.split(":", 1)[1]
        if symbol not in owned[ep.backend_file].provided_symbols:
            gaps.append(f"entrypoints.backend_import symbol not provided: {symbol}")
    return gaps

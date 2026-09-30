"""Replay the original task-management contract through production CodeAgent.

Only model responses are deterministic recordings of the three Live-produced
owned files.  The original Live Trace and its contract are never modified.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.artifact.store import ArtifactStore
from app.bootstrap.runtime import build_container
from app.domain.architecture.implementation_contract import ImplementationContractStore
from app.domain.code.git_service import GitCodeStagingService, GitCodeIntegrationService
from app.domain.code.http_contract import validate_http_consumers
from app.orchestration.plan import ExecutionPlan
from app.workspace.git_repository import TaskBranch
from app.workflow.compiler import ImplementationContractCompiler

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "projects/live-l2-20260926-rerun/wanfa-live-l2-todo-20260927"
UNIT_FILES = {
    "task-management-repository": "backend/app/task_repository.py",
    "task-management-service": "backend/app/task_service.py",
    "task-management-http": "backend/app/main.py",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request(base: str, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base + path, body, method=method,
                      headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(request, timeout=2) as response:
            raw = response.read().decode()
            return {"status": response.status, "body": raw,
                    "json": json.loads(raw) if raw else None}
    except HTTPError as error:
        raw = error.read().decode()
        return {"status": error.code, "body": raw,
                "json": json.loads(raw) if raw else None}


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _contract(raw: dict) -> tuple[dict, list[str]]:
    """Freeze missing interfaces in an isolated *copy* of the full Live contract."""
    raw = json.loads(json.dumps(raw))
    amendments = [
        "entrypoints.backend_file/backend_import/backend_command frozen",
        "task_management.task_api POST/GET /tasks frozen; health GET /health",
        "TaskService and TaskRepository canonical interfaces and bindings frozen",
        "three selected units use current Replay contract rather than old Live staged refs",
        "Replay SQLite path is the recorded repository default: workspace/backend/tasks.sqlite3",
    ]
    raw["entrypoints"] = {
        "backend_file": "backend/app/main.py", "backend_import": "app.main:app",
        "backend_command": "uvicorn app.main:app", "health_path": "/health",
    }
    for interface in raw["interfaces"]:
        if interface["interface_id"] == "task_management.task_api":
            interface["operations"] = [
                {"method": "POST", "path": "/tasks"},
                {"method": "GET", "path": "/tasks"},
            ]
        elif interface["interface_id"] == "task_management.health":
            interface["operations"] = [{"method": "GET", "path": "/health"}]
    raw["interfaces"].extend([
        {"interface_id": "task_management.repository", "kind": "service",
         "name": "TaskRepository", "owner_unit": "task-management-repository",
         "owner_file": "backend/app/task_repository.py"},
        {"interface_id": "task_management.service", "kind": "service",
         "name": "TaskService", "owner_unit": "task-management-service",
         "owner_file": "backend/app/task_service.py"},
    ])
    for unit in raw["implementation_units"]:
        if unit["unit_id"] not in UNIT_FILES:
            continue
        unit["input_refs"] = []
        unit["slot"] = "backend"
        if unit["unit_id"] == "task-management-repository":
            unit["provides_interfaces"] = ["task_management.repository"]
            unit["provided_symbols"] = ["TaskRepository"]
        elif unit["unit_id"] == "task-management-service":
            unit["provides_interfaces"] = ["task_management.service"]
            unit["consumes_interfaces"] = ["task_management.repository"]
            unit["provided_symbols"] = ["TaskService"]
        else:
            unit["consumes_interfaces"] = ["task_management.service"]
            unit["provided_symbols"] = ["app"]
    return raw, amendments


class RecordedCrewAgent:
    calls: list[str] = []
    sources: dict[str, str] = {}
    prompts: dict[str, str] = {}

    def __init__(self, **kwargs: Any) -> None:
        pass

    def execute_task(self, task: Any) -> str:
        description = task["description"]
        found = re.search(r"实现单元:\s*(task-management-(?:repository|service|http))", description)
        if found is None:
            raise AssertionError("CodeAgent task did not identify a selected implementation unit")
        unit = found.group(1)
        type(self).calls.append(unit)
        type(self).prompts[unit] = description
        args = {"path": UNIT_FILES[unit], "content": self.sources[unit]}
        return "to=functions.write_staged_code_file code:\n" + json.dumps(args) + "\n已按合同交付。"


def _layering(project: Path) -> dict[str, object]:
    """Prove the concrete API -> Service -> Repository -> SQLite edges."""
    sources = {unit: (project / "workspace" / path).read_text(encoding="utf-8")
               for unit, path in UNIT_FILES.items()}
    trees = {unit: ast.parse(source, filename=UNIT_FILES[unit])
             for unit, source in sources.items()}
    def imports(unit: str, module: str, symbol: str) -> bool:
        return any(isinstance(node, ast.ImportFrom)
                   and node.module == module
                   and any(alias.name == symbol for alias in node.names)
                   for node in ast.walk(trees[unit]))
    api = trees["task-management-http"]
    service = trees["task-management-service"]
    repository = trees["task-management-repository"]
    checks = {
        "api_imports_service": imports("task-management-http", "app.task_service", "TaskService"),
        "service_imports_repository": imports("task-management-service", "task_repository", "TaskRepository"),
        "repository_imports_sqlite3": any(isinstance(node, ast.Import)
                 and any(alias.name == "sqlite3" for alias in node.names)
                 for node in ast.walk(repository)),
        "api_does_not_import_repository_or_sqlite":
            "TaskRepository" not in sources["task-management-http"]
            and "sqlite3" not in sources["task-management-http"],
        "service_does_not_access_sqlite": "sqlite3" not in sources["task-management-service"]
                                            and ".execute(" not in sources["task-management-service"],
        "repository_has_create_insert_select": all(
            token in sources["task-management-repository"].upper()
            for token in ("CREATE TABLE IF NOT EXISTS", "INSERT INTO TASKS", "SELECT ID, TITLE, COMPLETED")
        ),
        "api_routes_delegate_to_service": any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "task_service"
            and node.func.attr == "create_task"
            for node in ast.walk(api)
        ),
        "service_delegates_create_to_repository": any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
            and node.func.value.attr == "repository" and node.func.attr == "create_task"
            for node in ast.walk(service)
        ),
    }
    if not all(checks.values()):
        raise AssertionError(f"API -> Service -> Repository -> SQLite layering failed: {checks}")
    return {"status": "passed", "checks": checks,
            "source_sha256": {unit: sha(text.encode()) for unit, text in sources.items()}}


def run(project: Path, *, candidate_path: Path | None = None) -> dict[str, Any]:
    if project.exists():
        raise FileExistsError(f"Refuse to overwrite an existing replay project: {project}")
    project.mkdir(parents=True)
    original_path = LIVE / ".projectos/architecture/project-contract.json"
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes)
    candidate_bytes: bytes | None = None
    if candidate_path is None:
        amended, amendments = _contract(original)
    else:
        # The F revision is a read-only input. Only remove stale staged refs on
        # the three projected units; keep all nine units and every F constraint.
        candidate_bytes = candidate_path.read_bytes()
        amended = json.loads(candidate_bytes)
        amendments = ["versioned F candidate used unchanged except Replay-only selected unit refs/slot"]
        for unit in amended["implementation_units"]:
            if unit["unit_id"] in UNIT_FILES:
                unit["input_refs"] = []
                unit["slot"] = "backend"
        amendments.append("three selected units clear old Live staged refs for isolated Replay")
    contract = ImplementationContractStore(str(project)).save(amended)
    contract_bytes = (project / ImplementationContractStore.relative_path).read_bytes()
    fixture = {unit: (LIVE / "workspace" / path).read_text(encoding="utf-8")
               for unit, path in UNIT_FILES.items()}
    fixture_hashes = {unit: sha(content.encode()) for unit, content in fixture.items()}
    # Persist exact replay inputs so the output can be audited even if Live changes.
    fixture_dir = project / "recorded-provider-responses"
    for unit, content in fixture.items():
        destination = fixture_dir / UNIT_FILES[unit]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    container = build_container(str(project))
    trace = container.traces.start_trace("Replay L1b: task management business vertical slice")
    compiled = ImplementationContractCompiler().compile(
        contract, goal="Task management complete contract / selected business slice",
        plan_id=f"replay-l1b-{uuid4().hex[:8]}", trace=trace,
    )
    selected = {f"wi-code-{unit}" for unit in UNIT_FILES}
    members = [item for item in compiled.work_items if item.id in selected]
    if {item.id for item in members} != selected:
        raise AssertionError(f"Missing business units from full compilation: {selected - {item.id for item in members}}")
    # Only remove wave barriers to deliberately omitted non-business units.
    projected = [replace(item, dependencies=tuple(dep for dep in item.dependencies
               if dep.work_item_id in selected), contract_digest=None) for item in members]
    plan = ExecutionPlan(
        id=compiled.id, goal=compiled.goal, work_items=tuple(projected),
        template_id="replay-l1b-business", process_id=compiled.process_id,
        trace=trace, validation_scope="vertical_slice",
    )
    container.traces.record_plan(plan)
    container.traces.record_plan_baseline(plan)
    RecordedCrewAgent.calls = []
    RecordedCrewAgent.prompts = {}
    RecordedCrewAgent.sources = fixture
    with patch("app.agent.base_agent.Agent", RecordedCrewAgent), \
         patch("app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs), \
         patch("app.agent.base_agent.build_llm", return_value=object()):
        result = container.runner.run(plan)
    if result.status.value != "completed":
        raise RuntimeError(f"production CodeAgent/GraphRunner failed: {result.status.value}: {result.error}")
    if set(RecordedCrewAgent.calls) != set(UNIT_FILES) or len(RecordedCrewAgent.calls) != 3:
        raise AssertionError(f"unexpected CodeAgent calls: {RecordedCrewAgent.calls}")
    prompts = RecordedCrewAgent.prompts
    prompt_dir = project / "agent-prompts"
    prompt_dir.mkdir()
    for unit, text in prompts.items():
        (prompt_dir / f"{unit}.txt").write_text(text, encoding="utf-8")
    for consumer, module, symbol in (
        ("task-management-service", "app.task_repository", "TaskRepository"),
        ("task-management-http", "app.task_service", "TaskService"),
    ):
        if module not in prompts[consumer] or symbol not in prompts[consumer]:
            raise AssertionError(f"missing canonical binding in {consumer} task")
    staging = GitCodeStagingService(str(project))
    changes = {item.id: staging.load_change_set(trace.trace_id, item.id)
               for item in projected}
    for item in projected:
        change = changes[item.id]
        if change.changed_files != (f"workspace/{item.owned_files[0]}",):
            raise AssertionError(f"ownership mismatch {item.id}: {change.changed_files}")
    events = container.traces.list_events(trace.trace_id)
    waves = [event for event in events if event["type"] == "code_wave_integrated"]
    if len(waves) != 3:
        raise AssertionError(f"expected 3 Wave integrations, got {len(waves)}")
    commits = {}
    for event in waves:
        match = re.search(r"merge commit: ([0-9a-f]{40})", event["details"]["summary"])
        if not match:
            raise AssertionError(f"Wave event missing merge commit: {event}")
        commits[str(event["details"]["wave"])] = match.group(1)
    baseline = staging.load_baseline(trace.trace_id)
    if not all(change.commit in staging.integrated_commits(trace.trace_id)
               for change in changes.values()):
        raise AssertionError("not all ChangeSets recorded integrated")
    for event in waves:
        commit = commits[str(event["details"]["wave"])]
        kind = subprocess.run(["git", "cat-file", "-t", commit], cwd=project,
                              capture_output=True, text=True, check=False)
        if kind.returncode != 0 or kind.stdout.strip() != "commit":
            raise AssertionError(f"Wave references a missing Git commit: {commit}")
    for item in projected:
        changed = item.owned_files[0]
        if (project / "workspace" / changed).read_text() != fixture[item.implementation_unit_id]:
            raise AssertionError(f"Wave failed to publish exact recorded file {changed}")
    root = project / "workspace"
    branch = TaskBranch(trace.trace_id, "published", "", "", str(project))
    import_issues = GitCodeIntegrationService._validate_local_imports(branch)
    binding_issues = GitCodeIntegrationService(str(project))._validate_contract_bindings(branch)
    http_issues = validate_http_consumers(root, contract)
    layering = _layering(project)
    if import_issues or binding_issues or http_issues:
        raise AssertionError(f"gates failed: {import_issues}, {binding_issues}, {http_issues}")
    db = project / "workspace/backend/tasks.sqlite3"
    port = _port()
    base = f"http://127.0.0.1:{port}"
    responses: dict[str, Any] = {}
    logs: list[dict[str, Any]] = []
    for attempt in range(2):
        log_path = project / f"runtime-{attempt + 1}.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=root / "backend", env=os.environ.copy(),
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 12
                while True:
                    try:
                        health = _request(base, "GET", "/health")
                        if health["status"] == 200:
                            break
                    except (OSError, URLError):
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"runtime {attempt + 1} failed to start, log: {log_path}")
                    time.sleep(.1)
                if attempt == 0:
                    responses["health"] = health
                    responses["blank"] = _request(base, "POST", "/tasks", {"title": "   "})
                    responses["create"] = _request(base, "POST", "/tasks", {"title": "Replay L1b persisted"})
                    responses["list_before_restart"] = _request(base, "GET", "/tasks")
                else:
                    responses["health_after_restart"] = health
                    responses["list_after_restart"] = _request(base, "GET", "/tasks")
                logs.append({"pid": process.pid, "command": "python -m uvicorn app.main:app",
                             "log": str(log_path), "attempt": attempt + 1})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
    created = responses["create"]["json"]
    if not (responses["blank"]["status"] == 400
            and responses["create"]["status"] == 201
            and isinstance(created, dict) and created.get("id")
            and responses["list_before_restart"]["status"] == 200
            and responses["list_after_restart"]["status"] == 200
            and created in responses["list_before_restart"]["json"]
            and created in responses["list_after_restart"]["json"]):
        raise AssertionError(f"business smoke failed: {responses}")
    import sqlite3
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT id, title, completed FROM tasks WHERE id = ?", (created["id"],)).fetchone()
    if row is None or row[1] != created["title"]:
        raise AssertionError("SQLite row absent after process restart")
    container.traces.record_event(trace, "runtime", "runtime_business_smoke_passed",
                                  details={"responses": responses, "sqlite_row": row, "pids": [x["pid"] for x in logs]})
    evidence = {
        "status": "completed", "scope": "Replay L1b", "trace_id": trace.trace_id,
        "source_live_trace": "tr-2e4924e985cf", "source_contract_sha256": sha(original_bytes),
        "amended_replay_contract_sha256": sha(contract_bytes), "amendments": amendments,
        "input_candidate_path": str(candidate_path) if candidate_path else None,
        "input_candidate_sha256": sha(candidate_bytes) if candidate_bytes is not None else None,
        "f_constraints_not_proven_by_this_slice": (
            ["recorded repository does not read TASK_DB_PATH",
             "frontend package/Vite build and same-origin proxy not selected",
             "Dockerfile/Compose execution and persistent volume not selected"]
            if candidate_path is not None else []
        ),
        "source_provider_responses": "recorded Live-generated file contents; no external provider call",
        "recorded_response_sha256": fixture_hashes,
        "full_contract_units": len(contract.units),
        "full_compilation_items": len(compiled.work_items),
        "selected_work_items": [item.id for item in projected],
        "prompts_contain_canonical_bindings": True,
        "agent_prompts": {unit: {"path": str(prompt_dir / f"{unit}.txt"),
                                 "sha256": sha(text.encode())}
                          for unit, text in prompts.items()},
        "crewai_agent_calls": RecordedCrewAgent.calls,
        "changesets": {key: {"commit": change.commit, "files": list(change.changed_files)}
                       for key, change in changes.items()},
        "wave_merge_commits": commits, "baseline": baseline,
        "gates": {"ast_import": {"status": "passed", "issues": list(import_issues)},
                  "canonical_binding": {"status": "passed", "issues": list(binding_issues)},
                  "http_contract": {"status": "passed", "issues": list(http_issues)},
                  "layering": layering},
        "runtime": {"processes": logs, "responses": responses,
                    "sqlite_path": str(db), "sqlite_row_after_restart": row},
        "event_types": [event["type"] for event in container.traces.list_events(trace.trace_id)],
        "project_path": str(project), "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path,
                        help="Read-only versioned F candidate; never modifies original Live contract")
    args = parser.parse_args()
    candidate_path = args.candidate.resolve() if args.candidate else None
    evidence_dir = ROOT / "validation-evidence"
    evidence_dir.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="replay-l1b-project-", dir=evidence_dir))
    project.rmdir()  # run() creates it and refuses to overwrite old evidence.
    evidence = run(project, candidate_path=candidate_path)
    label = "replay-l1-business-f-candidate-" if candidate_path else "replay-l1-business-"
    name = f"{label}{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}.json"
    path = evidence_dir / name
    with path.open("x") as stream:
        stream.write(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n")
    # Separate gate artifacts point to the same persistent project and Trace.
    for prefix, payload in (
        ("import-binding-gate", {"ast_import": evidence["gates"]["ast_import"],
                                 "canonical_binding": evidence["gates"]["canonical_binding"],
                                 "layering": evidence["gates"]["layering"],
                                 "agent_prompts": evidence["agent_prompts"]}),
        ("http-contract-gate", {"http_contract": evidence["gates"]["http_contract"],
                                "declared_operations": [operation.as_dict() for interface in
                                    ImplementationContractStore(evidence["project_path"]).load().interfaces
                                    if interface.interface_id == "task_management.task_api"
                                    for operation in interface.operations]}),
        ("runtime-business-smoke", evidence["runtime"]),
    ):
        gate_path = evidence_dir / f"{prefix}-{evidence['trace_id']}.json"
        gate_path.write_text(json.dumps({"status": "passed", "trace_id": evidence["trace_id"],
            "source_evidence": str(path), "project_path": evidence["project_path"],
            **payload}, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"evidence_path": str(path), "project_path": str(project),
                      "status": evidence["status"], "trace_id": evidence["trace_id"],
                      "gates": evidence["gates"], "runtime": evidence["runtime"]["responses"]},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

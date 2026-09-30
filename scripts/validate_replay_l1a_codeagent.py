"""Deterministic Replay L1a through the production CodeAgent and GraphRunner.

Only the provider response is replayed. ProjectOS CodeAgent, Gateway tool policy,
CodeStaging, Git ChangeSet, Wave integration, and runtime smoke are real.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import patch
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap.runtime import build_container
from app.domain.architecture.implementation_contract import (
    EntrypointContract,
    ImplementationContract,
    ImplementationContractStore,
    ImplementationUnit,
)
from app.domain.code.git_service import GitCodeStagingService
from app.domain.code.service import CodeStagingService
from app.orchestration.plan import ExecutionPlan
from app.workflow.compiler import ImplementationContractCompiler


_HEALTH_APP = '''from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'''


class ReplayCrewAgent:
    """CrewAI-shaped adapter returning one serialized, recorded tool call."""
    calls = 0

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def execute_task(self, _task: Any) -> str:
        type(self).calls += 1
        args = {"path": "backend/app/main.py", "content": _HEALTH_APP}
        return "to=functions.write_staged_code_file code:\n" + json.dumps(args) + "\n已按合同交付。"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health(port: int) -> tuple[int, str] | None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            return response.status, response.read().decode("utf-8")
    except (OSError, URLError):
        return None


def run(project_path: str | Path) -> dict[str, Any]:
    project = Path(project_path).resolve()
    project.mkdir(parents=True, exist_ok=True)
    container = build_container(str(project))
    trace = container.traces.start_trace("Replay L1a production CodeAgent health slice")
    contract = ImplementationContract(
        schema_version=1,
        units=(ImplementationUnit(
            unit_id="health-entrypoint", layer="runtime",
            objective="提供 GET /health 并严格遵守冻结入口合同。",
            allowed_paths=("backend/app/**",),
            required_paths=("backend/app/main.py",),
            owned_files=("backend/app/main.py",), wave=0, slot="backend",
            acceptance_criteria=("GET /health 返回 200 且 JSON status=ok。",),
        ),),
        entrypoints=EntrypointContract(
            backend_file="backend/app/main.py", backend_import="backend.app.main:app",
            backend_command="uvicorn backend.app.main:app", health_path="/health",
        ),
        required_files=("backend/app/main.py",), layers=("runtime",),
        allowed_dependencies={"runtime": ()}, forbidden_imports={"runtime": ()},
        path_mapping={"runtime": ("backend/app/**",)},
    )
    ImplementationContractStore(str(project)).save(contract.as_dict())
    compiled = ImplementationContractCompiler().compile(
        contract, goal="Replay L1a health vertical slice",
        plan_id=f"replay-l1a-{uuid4().hex[:8]}", trace=trace,
    )
    if len(compiled.work_items) != 1:
        raise AssertionError(f"expected one CodeAgent WorkItem, got {len(compiled.work_items)}")
    item = compiled.work_items[0]
    plan = ExecutionPlan(
        id=compiled.id, goal=compiled.goal, work_items=compiled.work_items,
        template_id=compiled.template_id, process_id=compiled.process_id,
        trace=compiled.trace, validation_scope="vertical_slice",
    )
    container.traces.record_plan(plan)
    container.traces.record_plan_baseline(plan)
    ReplayCrewAgent.calls = 0
    with patch("app.agent.base_agent.Agent", ReplayCrewAgent), \
         patch("app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs), \
         patch("app.agent.base_agent.build_llm", return_value=object()):
        result = container.runner.run(plan)
    if result.status.value != "completed":
        raise RuntimeError(f"production CodeAgent/GraphRunner failed: {result.status.value}: {result.error}")
    change = CodeStagingService(str(project)).load_change_set(trace.trace_id, item.id)
    if tuple(change.changed_files) != ("workspace/backend/app/main.py",):
        raise AssertionError(f"CodeAgent violated single-file ownership: {change.changed_files}")
    # GraphRunner owns Wave integration.  Do not call integrate_wave a second
    # time here: the second call is intentionally a no-op after the runner has
    # refreshed the baseline, and its no-op message is not a merge commit.
    events = container.traces.list_events(trace.trace_id)
    wave_events = [
        event for event in events if event.get("type") == "code_wave_integrated"
    ]
    if len(wave_events) != 1:
        raise AssertionError(
            f"expected exactly one GraphRunner code_wave_integrated event, got {len(wave_events)}"
        )
    wave_event = wave_events[0]
    wave_details = wave_event.get("details", {})
    if not isinstance(wave_details, dict):
        raise AssertionError("code_wave_integrated event details are not an object")
    integration_summary = str(wave_details.get("summary", ""))
    merge_match = re.search(r"merge commit: ([0-9a-f]{40})", integration_summary)
    if merge_match is None:
        raise AssertionError(
            "GraphRunner Wave event does not contain a real 40-character merge commit: "
            + integration_summary
        )
    wave_merge_commit = merge_match.group(1)
    staging = GitCodeStagingService(str(project))
    integrated_commits = staging.integrated_commits(trace.trace_id)
    baseline = staging.load_baseline(trace.trace_id)
    if change.commit not in integrated_commits:
        raise AssertionError(
            f"ChangeSet commit {change.commit} was not recorded as integrated: "
            f"{sorted(integrated_commits)}"
        )
    merge_type = subprocess.run(
        ["git", "cat-file", "-t", wave_merge_commit],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge_type.returncode != 0 or merge_type.stdout.strip() != "commit":
        raise AssertionError(
            f"Wave summary references a non-existent commit: {wave_merge_commit}"
        )
    workspace_file = project / "workspace/backend/app/main.py"
    if not workspace_file.is_file():
        raise AssertionError("Wave did not publish owned file")
    merge_file = subprocess.run(
        ["git", "show", f"{wave_merge_commit}:workspace/backend/app/main.py"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge_file.returncode != 0:
        raise AssertionError(
            "Wave merge commit does not contain the owned implementation file: "
            + merge_file.stderr.strip()
        )
    if merge_file.stdout != workspace_file.read_text(encoding="utf-8"):
        raise AssertionError(
            "Published workspace file differs from the file in the recorded Wave merge commit"
        )
    port = _free_port()
    container.traces.record_event(
        trace,
        "runtime",
        "runtime_smoke_started",
        details={"health_path": "/health", "port": port},
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=project / "workspace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    response = None
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            response = _health(port)
            if response:
                break
            if process.poll() is not None:
                raise RuntimeError(f"runtime exited rc={process.returncode}: {process.stderr.read() if process.stderr else ''}")
            time.sleep(0.1)
        if response is None or response[0] != 200 or json.loads(response[1]).get("status") != "ok":
            raise AssertionError(f"/health failed: {response}")
        container.traces.record_event(
            trace,
            "runtime",
            "runtime_smoke_passed",
            details={"status": response[0], "body": response[1]},
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)
    events = container.traces.list_events(trace.trace_id)
    evidence = {
        "status": "completed", "scope": "Replay L1a", "trace_id": trace.trace_id,
        "provider_mode": "deterministic recorded CrewAI tool-call response; no external provider call",
        "agent": "production CodeAgent via AgentRegistry and GraphRunner",
        "crewai_agent_calls": ReplayCrewAgent.calls,
        "work_item_id": item.id, "owned_files": list(item.owned_files),
        "changeset_commit": change.commit, "changeset_files": list(change.changed_files),
        "wave_integration": integration_summary,
        "wave_merge_commit": wave_merge_commit,
        "wave_event_work_item_id": wave_event.get("work_item_id"),
        "integrated_commits": sorted(integrated_commits),
        "baseline": baseline,
        "workspace_file": str(workspace_file),
        "runtime_smoke": {"status": response[0], "body": response[1]},
        "event_types": [event["type"] for event in events],
        "assertions": {
            "actual_code_agent_registry_path": True,
            "gateway_serialized_tool_dispatch": True,
            "single_owned_file_changeset": True,
            "wave_integration": True,
            "changeset_recorded_as_integrated": True,
            "real_merge_commit": True,
            "merge_commit_published_to_workspace": True,
            "health_200": True,
            "runtime_smoke_passed": True,
        },
    }
    return evidence


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="projectos-replay-l1a-codeagent-") as directory:
        print(json.dumps(run(directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

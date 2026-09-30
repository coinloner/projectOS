"""Run a deterministic, real workspace vertical-slice delivery validation.

This is deliberately provider-independent. It validates the hand-off that a
live model run must eventually exercise:

    ImplementationContract -> CodeAgent work item -> Git ChangeSet
    -> Wave integration -> running HTTP smoke test

The script does not fake the Git layer or copy files directly into the final
workspace. It uses the same contract compiler and CodeStaging/Integration
services used by the delivery runner.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.artifact.repository import ArtifactRef
from app.domain.architecture.implementation_contract import (
    EntrypointContract,
    ImplementationContract,
    ImplementationContractStore,
    ImplementationUnit,
)
from app.domain.code.service import CodeIntegrationService, CodeStagingService
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.trace import TraceStore
from app.workflow.compiler import ImplementationContractCompiler


_HEALTH_APP = """\
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_request(port: int) -> tuple[int, str] | None:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            return response.status, response.read().decode("utf-8")
    except (OSError, URLError):
        return None


def run_vertical_slice(project_path: str | Path) -> dict[str, Any]:
    project = Path(project_path).resolve()
    project.mkdir(parents=True, exist_ok=True)
    traces = TraceStore(str(project))
    trace = traces.start_trace("health vertical slice")

    contract = ImplementationContract(
        schema_version=1,
        units=(
            ImplementationUnit(
                unit_id="health-entrypoint",
                layer="runtime",
                objective="提供 HTTP health endpoint",
                allowed_paths=("backend/app/**",),
                required_paths=("backend/app/main.py",),
                owned_files=("backend/app/main.py",),
                wave=0,
                slot="backend",
                acceptance_criteria=("GET /health 返回 200",),
            ),
        ),
        entrypoints=EntrypointContract(
            backend_file="backend/app/main.py",
            backend_import="backend.app.main:app",
            backend_command="uvicorn backend.app.main:app",
            health_path="/health",
        ),
        required_files=("backend/app/main.py",),
        layers=("runtime",),
        allowed_dependencies={"runtime": ()},
        forbidden_imports={"runtime": ()},
        path_mapping={"runtime": ("backend/app/**",)},
    )
    stored = ImplementationContractStore(str(project)).save(contract.as_dict())
    plan = ImplementationContractCompiler().compile(
        contract,
        goal="health vertical slice",
        plan_id="health-plan",
        trace=trace,
    )
    if len(plan.work_items) != 1:
        raise AssertionError(f"health slice must compile to one WorkItem, got {len(plan.work_items)}")
    item = plan.work_items[0]
    traces.record_plan(plan)
    traces.record_plan_baseline(plan)
    traces.record_event(
        trace,
        "control",
        "implementation_plan_compiled",
        details={
            "compiled_plan_digest": item.contract_digest,
            "implementation_unit_ids": [item.implementation_unit_id],
            "work_item_ids": [item.id],
            "wave_map": {item.implementation_unit_id or item.id: item.wave},
            "ownership_map": {item.implementation_unit_id or item.id: list(item.owned_files)},
            "dependency_map": {item.implementation_unit_id or item.id: list(item.dependency_ids)},
        },
    )
    traces.record_event(trace, item.id, "work_item_started")

    context = ExecutionContext(
        trace_id=trace.trace_id,
        work_item_id=item.id,
        agent_id="code_agent",
        contract_digest=item.contract_digest,
        execution_mode=ExecutionMode.PARTITIONED,
        input_refs=item.input_refs,
        slot=item.slot,
        allowed_paths=item.allowed_paths,
        forbidden_paths=item.forbidden_paths,
        required_paths=item.required_paths,
        implementation_unit_id=item.implementation_unit_id,
        owned_files=item.owned_files,
    )
    staging = CodeStagingService(str(project))
    staging_result = staging.write_staged_file(
        context,
        "backend/app/main.py",
        _HEALTH_APP,
    )
    change = staging.load_change_set(trace.trace_id, item.id)
    traces.record_event(
        trace,
        item.id,
        "work_item_completed",
        details={"change_set_commit": change.commit, "files": list(change.changed_files)},
    )

    integration_ref = ArtifactRef.staged(
        artifact_key="implementation",
        trace_id=trace.trace_id,
        work_item_id=item.id,
        slot=item.slot or "",
    )
    integration_context = ExecutionContext(
        trace_id=trace.trace_id,
        work_item_id="wave-0-integration",
        agent_id="code_integration_agent",
        execution_mode=ExecutionMode.INTEGRATION,
        input_refs=(integration_ref,),
        publish_target="workspace",
    )
    integration_result = CodeIntegrationService(str(project)).integrate_wave(
        integration_context
    )
    traces.record_event(
        trace,
        integration_context.work_item_id,
        "code_wave_integrated",
        details={"wave": 0, "work_item_ids": [item.id], "summary": integration_result},
    )
    baseline = staging.load_baseline(trace.trace_id)
    workspace_file = project / "workspace" / "backend" / "app" / "main.py"
    if not workspace_file.is_file():
        raise AssertionError("Wave integration did not publish backend/app/main.py")

    port = _free_port()
    traces.record_event(trace, "runtime", "runtime_smoke_started", details={"port": port})
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=project / "workspace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    response: tuple[int, str] | None = None
    try:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            response = _health_request(port)
            if response is not None:
                break
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise AssertionError(
                    f"runtime exited before smoke test: rc={process.returncode}; stderr={stderr}"
                )
            time.sleep(0.1)
        if response is None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"/health smoke test timed out; stderr={stderr}")
        status, body = response
        if status != 200 or json.loads(body).get("status") != "ok":
            raise AssertionError(f"unexpected /health response: {status} {body}")
        traces.record_event(
            trace,
            "runtime",
            "runtime_smoke_passed",
            details={"status": status, "body": body},
        )
        traces.finish_trace(trace, "completed")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    return {
        "status": "completed",
        "trace_id": trace.trace_id,
        "contract_units": len(stored.units),
        "compiled_work_items": [item.id for item in plan.work_items],
        "changeset_commit": change.commit,
        "changeset_files": list(change.changed_files),
        "staging_result": staging_result,
        "integration_result": integration_result,
        "baseline": baseline,
        "workspace_file": str(workspace_file.relative_to(project)),
        "smoke": {"status": response[0], "body": response[1]},
        "event_types": [event["type"] for event in traces.list_events(trace.trace_id)],
        "code_wave_integrated": True,
        "runtime_smoke_passed": True,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="projectos-vertical-") as directory:
        summary = run_vertical_slice(directory)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

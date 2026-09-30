"""Run one real Wanfa CodeAgent after a real architecture publication.

This is the Live L1 gate.  It deliberately stops after one compiled CodeAgent
unit, then performs the real ChangeSet -> Wave 0 -> /health hand-off.  It is
separate from Live L0 so a provider retry or an implementation fault cannot
rewrite the architecture evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from app.architecture_execution_config import ArchitectureExecutionConfig
from app.artifact.store import ArtifactStore
from app.bootstrap.runtime import build_container
from app.domain.architecture.implementation_contract import (
    EntrypointContract,
    ImplementationContract,
    ImplementationContractStore,
    ImplementationUnit,
)
from app.domain.code.service import CodeIntegrationService, CodeStagingService
from app.execution_context import ExecutionMode
from app.llm.config import LLMSelection
from app.llm.preflight import ProviderPreflight, ProviderPreflightError
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import WorkItem
from app.project.project import Project
from app.workflow.compiler import ImplementationContractCompiler


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


def _selection() -> LLMSelection:
    return LLMSelection(
        provider="wanfa", model="gpt-6-sol", base_url="https://wanfaai.com",
        api_key_env="wanfa_API_KEY", crewai_provider="openai", wire_api="responses",
    )


def _prepare_project(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    Project.create_at(str(target), name=target.name)
    # Carry only published L0 evidence into a new Trace. Never copy the old
    # run/checkpoint/retry ledger, otherwise L1 would not be a fresh attempt.
    for name in ("requirement.md", "architecture.md", "project.yaml", "runtime.yaml", "docker-compose.yml"):
        source_file = source / name
        if source_file.is_file():
            shutil.copy2(source_file, target / name)
    ArtifactStore(str(target)).save("requirement", (target / "requirement.md").read_text(encoding="utf-8"))
    ArtifactStore(str(target)).save("architecture", (target / "architecture.md").read_text(encoding="utf-8"))
    ArtifactStore(str(target)).save(
        "environment",
        "# Environment\n\n- profile: fastapi-python\n- image: python:3.12-slim\n- status: ready\n",
    )


def _contract() -> ImplementationContract:
    return ImplementationContract(
        schema_version=1,
        compilation_strategy="file",
        units=(ImplementationUnit(
            unit_id="live-health-api",
            layer="api",
            objective=("实现一个最小可启动的 FastAPI HTTP 入口，提供 /health，"
                       "并严格按当前架构的后端入口契约落盘。"),
            allowed_paths=("backend/app/**",),
            required_paths=("backend/app/main.py",),
            owned_files=("backend/app/main.py",),
            wave=0,
            slot="backend",
            acceptance_criteria=("GET /health 返回 HTTP 200 和 JSON status=ok。",),
            constraints=("只修改 backend/app/main.py；不要添加其他文件。",),
            non_goals=("不实现完整 Todo 业务，不修改 frontend 或测试。",),
        ),),
        entrypoints=EntrypointContract(
            backend_file="backend/app/main.py",
            backend_import="backend.app.main:app",
            backend_command="uvicorn backend.app.main:app",
            health_path="/health",
        ),
        required_files=("backend/app/main.py",),
        layers=("api",),
        allowed_dependencies={"api": ()},
        forbidden_imports={"api": ()},
        path_mapping={"api": ("backend/app/**",)},
    )


def run_live_l1(source: str | Path, target: str | Path) -> dict[str, Any]:
    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    if not (source_path / "validation-evidence.json").is_file():
        raise FileNotFoundError(f"Live L0 evidence missing: {source_path}")
    selection = _selection()
    try:
        preflight = ProviderPreflight().check(selection)
    except ProviderPreflightError as error:
        # No project/Trace is created before this gate. A transient transport
        # failure is a provider-blocked attempt, not an implementation result.
        raise RuntimeError("Live L1 provider preflight blocked: " + json.dumps(error.result.as_dict(), ensure_ascii=False)) from error
    _prepare_project(source_path, target_path)
    config = ArchitectureExecutionConfig(scheme="D", candidate_strategy="all_or_nothing")
    container = build_container(str(target_path), llm_selection=selection, architecture_config=config)
    trace = container.traces.start_trace("Wanfa Live L1 real CodeAgent vertical slice")
    container.traces.set_llm_selection(trace.trace_id, selection)
    container.traces.set_architecture_config(trace.trace_id, config)
    stored = ImplementationContractStore(str(target_path)).save(_contract().as_dict())
    contract = ImplementationContractStore(str(target_path)).load()
    compiled_plan = ImplementationContractCompiler().compile(
        contract, goal="Wanfa Live L1 health vertical slice", plan_id=f"live-l1-{uuid4().hex[:8]}", trace=trace,
        semantic_units=False,
    )
    plan = ExecutionPlan(
        id=compiled_plan.id, goal=compiled_plan.goal,
        work_items=compiled_plan.work_items, template_id=compiled_plan.template_id,
        process_id=compiled_plan.process_id, trace=compiled_plan.trace,
        validation_scope="vertical_slice",
    )
    if len(plan.work_items) != 1:
        raise AssertionError(f"expected one compiled CodeAgent WorkItem, got {len(plan.work_items)}")
    item = plan.work_items[0]
    container.traces.record_plan(plan)
    container.traces.record_plan_baseline(plan)
    container.traces.record_event(trace, "control", "implementation_plan_compiled", details={
        "compiled_plan_digest": item.contract_digest,
        "implementation_unit_ids": [item.implementation_unit_id],
        "work_item_ids": [item.id],
        "wave_map": {item.implementation_unit_id or item.id: item.wave},
        "ownership_map": {item.implementation_unit_id or item.id: list(item.owned_files)},
        "dependency_map": {item.implementation_unit_id or item.id: list(item.dependency_ids)},
        "provider": selection.as_dict(),
        "source_l0": str(source_path),
    })
    result = container.runner.run(plan)
    if result.status.value != "completed":
        raise RuntimeError(f"real CodeAgent did not complete: {result.status.value}: {result.error}")
    change = CodeStagingService(str(target_path)).load_change_set(trace.trace_id, item.id)
    integration_context = __import__("app.execution_context", fromlist=["ExecutionContext"]).ExecutionContext(
        trace_id=trace.trace_id, work_item_id="wi-live-l1-wave-0", agent_id="code_integration_agent",
        execution_mode=ExecutionMode.INTEGRATION,
        input_refs=(__import__("app.artifact.repository", fromlist=["ArtifactRef"]).ArtifactRef.staged(
            artifact_key="implementation", trace_id=trace.trace_id, work_item_id=item.id, slot=item.slot or "backend"
        ),), publish_target="workspace",
    )
    integration_result = CodeIntegrationService(str(target_path)).integrate_wave(integration_context)
    container.traces.record_event(trace, integration_context.work_item_id, "code_wave_integrated", details={
        "wave": 0, "work_item_ids": [item.id], "summary": integration_result,
    })
    workspace_file = target_path / "workspace" / "backend" / "app" / "main.py"
    if not workspace_file.is_file():
        raise AssertionError("Wave 0 did not publish backend/app/main.py")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=target_path / "workspace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    response = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            response = _health(port)
            if response is not None:
                break
            if process.poll() is not None:
                raise RuntimeError(f"runtime exited before /health: rc={process.returncode}; stderr={process.stderr.read() if process.stderr else ''}")
            time.sleep(0.15)
        if response is None or response[0] != 200:
            raise RuntimeError(f"runtime /health failed: {response}")
        container.traces.record_event(trace, "runtime", "runtime_smoke_passed", details={"status": response[0], "body": response[1]})
        container.traces.finish_trace(trace, "completed")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)
    evidence = {
        "status": "completed", "scope": "Live L1", "trace_id": trace.trace_id,
        "project_path": str(target_path), "source_l0": str(source_path),
        "provider": selection.as_dict(), "provider_preflight": preflight.as_dict(),
        "contract_units": len(stored.units), "compiled_work_items": [item.id for item in plan.work_items],
        "changeset_commit": change.commit, "changeset_files": list(change.changed_files),
        "wave_0": {"integration": integration_result, "workspace_file": str(workspace_file.relative_to(target_path))},
        "runtime_smoke": {"status": response[0], "body": response[1]},
        "event_types": [event["type"] for event in container.traces.list_events(trace.trace_id)],
    }
    (target_path / "validation-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source-l0", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    if not os.environ.get("wanfa_API_KEY"):
        raise SystemExit("wanfa_API_KEY is not configured")
    print(json.dumps(run_live_l1(args.source_l0, args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

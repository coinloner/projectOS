"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
import os
from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from dotenv import dotenv_values

from app.application.runs import RunCoordinator, RunService
from app.bootstrap.runtime import build_container
from app.application.conversations import (
    ConversationBusy,
    ConversationService,
    ConversationStore,
)
from app.application.project_runtime import ProjectRuntimeService
from app.application.environment import EnvironmentProvisioner
from app.memory.store import MemoryStore
from app.sandbox.application_runner import ApplicationRunError
from app.sandbox.controller import SandboxController
from app.orchestration.trace import TraceStore
from app.orchestration.progress import canonical_progress
from app.planner.service import PlannerFailure
from app.project.project import Project
from app.project.paths import ProjectPathRegistry
from app.runtime.startup import ensure_startup_scripts
from app.runtime.local_status import LocalRuntimeStatusStore
from app.skill.module import SkillModule
from app.runtime.port_lifecycle import PortLifecycleManager
from app.llm.config import (
    LLMSelection,
    available_provider_configs,
    discover_provider_models,
    resolve_llm_selection,
)


_PROJECT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_AGENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _planner_failure_detail(error: PlannerFailure) -> object:
    """Expose structured diagnostics when a planning Trace exists.

    Preflight/control-plane PlannerFailure instances (for example an unknown
    workflow) have no Trace or signal yet, so retain the legacy string detail
    contract for those callers.
    """
    if error.trace_id is None and error.signal is None:
        return str(error)
    payload: dict[str, object] = {"message": str(error)}
    if error.trace_id:
        payload["trace_id"] = error.trace_id
    if error.signal is not None:
        payload["failure"] = error.signal.as_dict()
    return payload


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str | None = Field(default=None, min_length=1, max_length=1024)


class ProjectImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1024)


class LLMSelectionRequest(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)


class StartRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    workflow_id: str = Field(min_length=1, max_length=100)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    llm_overrides: dict[str, LLMSelectionRequest] = Field(default_factory=dict)


class MemoryDecisionRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=128)


class ConversationMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    llm_overrides: dict[str, LLMSelectionRequest] = Field(default_factory=dict)


class CapabilityApprovalRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=128)
    scope: Literal["node", "trace", "project"] = "trace"


class ResumeRequest(BaseModel):
    source_name: str | None = Field(default=None, min_length=1, max_length=128)


class CancelRunRequest(BaseModel):
    reason: str = Field(default="api_request", min_length=1, max_length=500)


class AgentSkillRequest(BaseModel):
    refs: list[str] = Field(default_factory=list, max_length=12)


class ProjectSkillRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


def create_app(*, projects_root: str = "./projects") -> FastAPI:
    """创建 HTTP 应用，不在 import 时创建项目或发起 Agent 执行。"""
    root = Path(projects_root).resolve()
    paths = ProjectPathRegistry(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            run_workers = int(os.environ.get("PROJECTOS_RUN_MAX_WORKERS", "4"))
        except (TypeError, ValueError):
            run_workers = 4
        coordinator = RunCoordinator(max_workers=max(1, run_workers))
        app.state.coordinator = coordinator
        app.state.run_service = RunService(coordinator=coordinator)
        app.state.conversations = ConversationService(
            run_service=app.state.run_service
        )
        app.state.project_runtime = ProjectRuntimeService()
        yield
        app.state.project_runtime.shutdown()
        coordinator.shutdown()

    app = FastAPI(
        title="ProjectOS API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/llm/providers")
    def list_llm_providers() -> dict[str, object]:
        """返回 UI 可选择的非敏感模型配置，不返回任何 key 内容。"""
        file_values = dotenv_values(".env")
        providers = [
            {
                **config,
                "key_configured": bool(
                    os.environ.get(config["api_key_env"])
                    or file_values.get(config["api_key_env"])
                ),
            }
            for config in available_provider_configs()
        ]
        return {"providers": providers}

    @app.get("/api/v1/llm/providers/{provider}/models")
    def list_llm_models(provider: str) -> dict[str, object]:
        """从 provider 动态读取模型 ID，供前端选择；不返回密钥。"""
        try:
            models = discover_provider_models(provider)
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {"provider": provider.strip().lower(), "models": list(models)}

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED)
    def create_project(payload: ProjectCreateRequest) -> dict[str, str]:
        project_id = _project_id_or_422(payload.name)
        project_path = (
            paths.default_path(project_id)
            if payload.path is None
            else Path(payload.path).expanduser().resolve()
        )
        if project_path == root or project_path == root.parent:
            raise HTTPException(status_code=422, detail="项目路径不能是项目根目录或其父目录")
        if project_path.exists():
            raise HTTPException(status_code=409, detail="项目已存在")
        Project.create_at(str(project_path), name=project_id)
        ensure_startup_scripts(str(project_path), project_id=project_id)
        paths.register(project_id, project_path)
        return {"project_id": project_id, "path": str(project_path), "status": "created"}

    @app.post("/api/v1/projects/import", status_code=status.HTTP_201_CREATED)
    def import_project(payload: ProjectImportRequest) -> dict[str, str]:
        """登记已存在的 ProjectOS 项目目录，不改写其中任何文件。"""
        project_id = _project_id_or_422(payload.name)
        project_path = Path(payload.path).expanduser().resolve()
        if project_path == root or project_path == root.parent:
            raise HTTPException(status_code=422, detail="项目路径不能是项目根目录或其父目录")
        if not project_path.is_dir() or not (project_path / "project.yaml").is_file():
            raise HTTPException(status_code=422, detail="导入目录不是有效的 ProjectOS 项目")
        existing = paths.resolve(project_id)
        if existing.exists() and existing != project_path:
            raise HTTPException(status_code=409, detail="项目 ID 已映射到其他目录")
        paths.register(project_id, project_path)
        ensure_startup_scripts(str(project_path), project_id=project_id)
        return {"project_id": project_id, "path": str(project_path), "status": "imported"}

    @app.get("/api/v1/projects/{project_id}/workflows")
    def list_workflows(project_id: str, request: Request) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        service: RunService = request.app.state.run_service
        return {
            "project_id": project_id,
            "workflows": service.controlled_workflows(str(project_path)),
        }

    @app.get("/api/v1/projects/{project_id}/skills")
    def list_project_skills(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        module = SkillModule(str(project_path))
        return {
            "project_id": project_id,
            "skills": list(module.list_skills()),
            "assignments": [item.as_dict() for item in module.assignments()],
        }

    @app.get("/api/v1/projects/{project_id}/agents/{agent_id}/skills")
    def get_agent_skills(project_id: str, agent_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        container = build_container(str(project_path))
        if container.agents.definition(agent_id) is None:
            raise HTTPException(status_code=404, detail=f"未注册 Agent: {agent_id}")
        module = SkillModule(str(project_path))
        return {"project_id": project_id, "agent_id": agent_id, "refs": list(module.refs_for(agent_id))}

    @app.put("/api/v1/projects/{project_id}/skills/{skill_ref}")
    def save_project_skill(
        project_id: str, skill_ref: str, payload: ProjectSkillRequest
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        try:
            skill = SkillModule(str(project_path)).save_project_skill(skill_ref, payload.content)
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"project_id": project_id, "skill": skill, "status": "updated"}

    @app.put("/api/v1/projects/{project_id}/agents/{agent_id}/skills")
    def assign_agent_skills(
        project_id: str, agent_id: str, payload: AgentSkillRequest
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        container = build_container(str(project_path))
        if container.agents.definition(agent_id) is None:
            raise HTTPException(status_code=404, detail=f"未注册 Agent: {agent_id}")
        try:
            assignment = SkillModule(str(project_path)).assign(agent_id, payload.refs)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"project_id": project_id, **assignment.as_dict(), "status": "updated"}

    @app.get("/api/v1/projects/{project_id}/runtime/preflight")
    def runtime_preflight(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        from app.policy.quality import ProjectRuntimePreflight
        static = ProjectRuntimePreflight().evaluate(str(project_path))
        return {
            "project_id": project_id,
            "sandbox": SandboxController().preflight(str(project_path)),
            "project": {
                "policy_id": static.policy_id,
                "passed": static.passed,
                "issues": [
                    {
                        "rule_id": issue.rule_id,
                        "summary": issue.summary,
                        "evidence_refs": list(issue.evidence_refs),
                    }
                    for issue in static.issues
                ],
            },
        }

    @app.get("/api/v1/projects/{project_id}/runtime/status")
    def runtime_status(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        return {
            "project_id": project_id,
            "environment": EnvironmentProvisioner().status(str(project_path)),
            "local": LocalRuntimeStatusStore().read(str(project_path)),
        }

    @app.get("/api/v1/projects/{project_id}/runtime/ports")
    def runtime_ports(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        manager = PortLifecycleManager(
            str(project_path / ".projectos" / "runtime" / "port-leases.json")
        )
        return {
            "project_id": project_id,
            "registry": manager.registry_path,
            "leases": [lease.as_dict() for lease in manager.active_leases()],
        }

    @app.get("/api/v1/projects/{project_id}/runtime/local-status")
    def local_runtime_status(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        return {
            "project_id": project_id,
            "runtime": LocalRuntimeStatusStore().read(str(project_path)),
        }

    @app.get("/api/v1/projects/{project_id}/runtime/dependency-approvals")
    def dependency_approval_status(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        return {
            "project_id": project_id,
            "approval": EnvironmentProvisioner().dependency_approval(str(project_path)),
        }

    @app.post("/api/v1/projects/{project_id}/runtime/dependency-approvals/approve", status_code=status.HTTP_202_ACCEPTED)
    def approve_dependency_resolution(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        try:
            result = EnvironmentProvisioner().approve_dependencies(str(project_path))
        except (FileNotFoundError, PermissionError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"project_id": project_id, **result}

    @app.post("/api/v1/projects/{project_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_run(
        project_id: str, payload: StartRunRequest, request: Request
    ) -> dict[str, str]:
        project_path = _project_path(root, project_id)
        service: RunService = request.app.state.run_service
        try:
            selection = (
                resolve_llm_selection(
                    payload.provider,
                    model=payload.model,
                    base_url=payload.base_url,
                )
                if any((payload.provider, payload.model, payload.base_url))
                else None
            )
            overrides = _resolve_llm_overrides(payload.llm_overrides)
            started = service.start_controlled_workflow(
                project_path=str(project_path),
                goal=payload.goal,
                workflow_id=payload.workflow_id,
                llm_selection=selection,
                llm_overrides=overrides,
            )
        except PlannerFailure as error:
            raise HTTPException(
                status_code=422, detail=_planner_failure_detail(error)
            ) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "trace_id": started.trace_id,
            "plan_id": started.plan_id,
            "workflow_id": started.workflow_id,
            "status": started.status,
        }

    @app.post("/api/v1/projects/{project_id}/conversations", status_code=status.HTTP_201_CREATED)
    def create_conversation(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        conversation = ConversationStore(str(project_path)).create(project_id)
        return conversation.as_dict()

    @app.get("/api/v1/projects/{project_id}/conversations/{conversation_id}")
    def get_conversation(
        project_id: str, conversation_id: str, request: Request
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        try:
            conversation = ConversationStore(str(project_path)).load(conversation_id)
            turns = request.app.state.conversations.sync_terminal_turns(
                str(project_path), conversation_id
            )
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            **conversation.as_dict(),
            "messages": [turn.as_dict() for turn in turns],
        }

    @app.post("/api/v1/projects/{project_id}/conversations/{conversation_id}/messages", status_code=status.HTTP_202_ACCEPTED)
    def send_conversation_message(
        project_id: str,
        conversation_id: str,
        payload: ConversationMessageRequest,
        request: Request,
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        service: ConversationService = request.app.state.conversations
        try:
            selection = (
                resolve_llm_selection(
                    payload.provider,
                    model=payload.model,
                    base_url=payload.base_url,
                )
                if any((payload.provider, payload.model, payload.base_url))
                else None
            )
            overrides = _resolve_llm_overrides(payload.llm_overrides)
            action = service.send_message(
                project_path=str(project_path),
                conversation_id=conversation_id,
                content=payload.content,
                llm_selection=selection,
                llm_overrides=overrides,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PlannerFailure as error:
            raise HTTPException(
                status_code=422, detail=_planner_failure_detail(error)
            ) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "intent": action.intent.value,
            "message": action.user_turn.as_dict(),
            "assistant_message": action.message.as_dict() if action.message else None,
            "trace_id": action.started_run.trace_id if action.started_run else action.trace_id,
            "plan_id": action.started_run.plan_id if action.started_run else None,
            "workflow_id": action.started_run.workflow_id if action.started_run else None,
            "status": action.started_run.status if action.started_run else "no_run",
        }

    @app.get("/api/v1/projects/{project_id}/conversations/{conversation_id}/messages")
    def list_conversation_messages(
        project_id: str, conversation_id: str, request: Request
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        try:
            turns = request.app.state.conversations.sync_terminal_turns(
                str(project_path), conversation_id
            )
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "conversation_id": conversation_id,
            "messages": [turn.as_dict() for turn in turns],
        }

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}")
    def get_run(project_id: str, trace_id: str, request: Request) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            trace = traces.load_trace(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        coordinator: RunCoordinator = request.app.state.coordinator
        progress = traces.load_progress(trace_id)
        progress = canonical_progress(progress)
        return {
            **trace,
            "worker_process_state": coordinator.status(trace_id) or "not_observed",
            "progress": progress,
        }

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/metrics")
    def get_run_metrics(project_id: str, trace_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            traces.load_trace(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        from app.orchestration.delivery import DeliveryStore
        metrics = traces.metrics(trace_id)
        delivery = DeliveryStore(str(project_path))
        metrics["coverage"] = delivery.load_matrix().coverage_summary()
        metrics["quality_dimensions"] = delivery.load_quality_matrix().as_dict()["dimensions"]
        return metrics

    @app.post(
        "/api/v1/projects/{project_id}/runs/{trace_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def resume_run(
        project_id: str, trace_id: str, request: Request,
        payload: ResumeRequest | None = Body(default=None),
    ) -> dict[str, str]:
        project_path = _project_path(root, project_id)
        service: RunService = request.app.state.run_service
        try:
            resumed = service.resume_run(
                project_path=str(project_path),
                trace_id=trace_id,
                source_name=payload.source_name if payload else None,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (PlannerFailure, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "trace_id": resumed.trace_id,
            "plan_id": resumed.plan_id,
            "workflow_id": resumed.workflow_id,
            "status": resumed.status,
        }

    @app.post(
        "/api/v1/projects/{project_id}/runs/{trace_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def cancel_run(
        project_id: str,
        trace_id: str,
        request: Request,
        payload: CancelRunRequest | None = Body(default=None),
    ) -> dict[str, str]:
        project_path = _project_path(root, project_id)
        try:
            return request.app.state.coordinator.cancel(
                str(project_path),
                trace_id,
                reason=payload.reason if payload else "api_request",
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/v1/projects/{project_id}/runs/{trace_id}/capabilities/approve",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def approve_run_capability(
        project_id: str,
        trace_id: str,
        payload: CapabilityApprovalRequest,
        request: Request,
    ) -> dict[str, str]:
        """批准一个候选 source，并立即从 checkpoint 恢复运行。"""
        project_path = _project_path(root, project_id)
        try:
            resumed = request.app.state.run_service.resume_run(
                project_path=str(project_path),
                trace_id=trace_id,
                source_name=payload.source_name,
                scope=payload.scope,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (PlannerFailure, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "trace_id": resumed.trace_id,
            "plan_id": resumed.plan_id,
            "workflow_id": resumed.workflow_id,
            "status": resumed.status,
            "approved_source": payload.source_name,
            "scope": payload.scope,
        }

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/capabilities")
    def list_run_capabilities(
        project_id: str, trace_id: str, request: Request
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            trace = traces.load_trace(trace_id)
            events = traces.list_events(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        waiting = next(
            (event for event in reversed(events) if event.get("type") == "work_item_waiting_capability"),
            None,
        )
        from app.tool_manager.grants import CapabilityGrantStore
        grants = [grant.as_dict() for grant in CapabilityGrantStore(str(project_path), trace_id).list(include_inactive=True)]
        if waiting is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": grants, "candidates": []}
        plan = traces.load_plan(trace_id)
        item = plan.work_item(str(waiting.get("work_item_id", "")))
        if item is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": grants, "candidates": []}
        container = build_container(str(project_path))
        definition = container.agents.definition(item.agent_id)
        if definition is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": grants, "candidates": []}
        capability = str(waiting.get("details", {}).get("capability", ""))
        candidates = container.gateway.find_sources_for_capability(
            definition.domain, capability
        )
        return {
            "trace_id": trace_id,
            "status": trace.get("status"),
            "work_item_id": item.id,
            "capability": capability,
            "reason": waiting.get("details", {}).get("reason"),
            "candidates": [source.source_name for source in candidates],
            "capabilities": grants,
        }

    @app.post("/api/v1/projects/{project_id}/runs/{trace_id}/capabilities/{grant_id}/revoke", status_code=status.HTTP_202_ACCEPTED)
    def revoke_run_capability(project_id: str, trace_id: str, grant_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        from app.tool_manager.grants import CapabilityGrantStore
        try:
            grant = CapabilityGrantStore(str(project_path), trace_id).revoke(grant_id)
            TraceStore(str(project_path)).record_event(
                TraceStore(str(project_path)).load_plan(trace_id).trace,
                "control", "capability_revoked", details={"grant_id": grant_id},
            )
        except (FileNotFoundError, KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"trace_id": trace_id, "grant": grant.as_dict()}

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/events")
    def get_run_events(project_id: str, trace_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            traces.load_trace(trace_id)
            events = traces.list_events(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"trace_id": trace_id, "events": events}

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/progress")
    def get_run_progress(project_id: str, trace_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            trace = traces.load_trace(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        progress = traces.load_progress(trace_id)
        progress = canonical_progress(progress)
        return {
            "trace_id": trace_id,
            "status": trace.get("status"),
            "progress": progress,
        }

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/baseline")
    def get_run_baseline(project_id: str, trace_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        traces = TraceStore(str(project_path))
        try:
            baseline = traces.load_plan_baseline(trace_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return baseline

    @app.get("/api/v1/projects/{project_id}/runs/{trace_id}/memory")
    def list_memory(
        project_id: str,
        trace_id: str,
        work_item_id: str | None = None,
        query: str = "",
        after_sequence: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        if after_sequence < 0 or limit < 1 or limit > 500:
            raise HTTPException(status_code=422, detail="after_sequence 或 limit 无效")
        traces = TraceStore(str(project_path))
        try:
            traces.load_trace(trace_id)
            store = MemoryStore(str(project_path))
            events = (
                store.search(
                    query,
                    trace_id=trace_id,
                    work_item_id=work_item_id,
                    limit=limit,
                )
                if query.strip()
                else store.events(
                    trace_id,
                    work_item_id=work_item_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
            )
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "trace_id": trace_id,
            "work_item_id": work_item_id,
            "query": query,
            "latest_sequence": store.latest_sequence(trace_id),
            "messages": [event.as_dict() for event in events],
        }

    @app.get("/api/v1/projects/{project_id}/memory/candidates")
    def list_memory_candidates(
        project_id: str, limit: int = 100
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        if limit < 1 or limit > 500:
            raise HTTPException(status_code=422, detail="limit 无效")
        store = MemoryStore(str(project_path))
        candidates = store.durable_candidates(limit=limit)
        return {
            "project_id": project_id,
            "candidates": [event.as_dict() for event in candidates],
        }

    @app.post("/api/v1/projects/{project_id}/memory/candidates/{event_id}/promote")
    def promote_memory_candidate(
        project_id: str, event_id: str, payload: MemoryDecisionRequest
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        store = MemoryStore(str(project_path))
        try:
            traces = TraceStore(str(project_path))
            traces.load_trace(payload.trace_id)
            promoted = store.promote(event_id, trace_id=payload.trace_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"event": promoted.as_dict()}

    @app.post("/api/v1/projects/{project_id}/memory/candidates/{event_id}/expire")
    def expire_memory_candidate(
        project_id: str, event_id: str, payload: MemoryDecisionRequest
    ) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        store = MemoryStore(str(project_path))
        try:
            traces = TraceStore(str(project_path))
            traces.load_trace(payload.trace_id)
            if event_id not in {
                event.id for event in store.durable_candidates(trace_id=payload.trace_id)
            }:
                raise ValueError("只有当前 Trace 的 durable candidate 可以过期")
            expired = store.expire(event_id, trace_id=payload.trace_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"event": expired.as_dict()}

    @app.post("/api/v1/projects/{project_id}/runtime/runs", status_code=status.HTTP_202_ACCEPTED)
    def start_project_runtime(
        project_id: str, request: Request
    ) -> dict[str, object]:
        """唯一的项目应用启动入口；不接受命令、镜像、端口或挂载参数。"""
        project_path = _project_path(root, project_id)
        runtime: ProjectRuntimeService = request.app.state.project_runtime
        try:
            return runtime.start(str(project_path)).as_dict()
        except (ApplicationRunError, FileNotFoundError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/projects/{project_id}/runtime/runs/{run_id}")
    def get_project_runtime(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, object]:
        _project_path(root, project_id)
        runtime: ProjectRuntimeService = request.app.state.project_runtime
        try:
            return runtime.status(run_id).as_dict()
        except ApplicationRunError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/api/v1/projects/{project_id}/runtime/runs/{run_id}")
    def stop_project_runtime(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, object]:
        _project_path(root, project_id)
        runtime: ProjectRuntimeService = request.app.state.project_runtime
        try:
            return runtime.stop(run_id).as_dict()
        except ApplicationRunError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return app


def _project_id_or_422(project_id: str) -> str:
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(
            status_code=422,
            detail="project_id 只能包含字母、数字、下划线和连字符，且必须以字母开头",
        )
    return project_id


def _resolve_llm_overrides(
    payload: dict[str, LLMSelectionRequest],
) -> dict[str, LLMSelection]:
    if len(payload) > 16:
        raise ValueError("每次运行最多配置 16 个 Agent 的 LLM 覆盖")
    result: dict[str, LLMSelection] = {}
    for agent_id, request in payload.items():
        if not _AGENT_ID.fullmatch(agent_id):
            raise ValueError(f"Agent ID 格式无效: {agent_id}")
        if not any((request.provider, request.model, request.base_url)):
            raise ValueError(f"Agent '{agent_id}' 的 LLM 覆盖不能为空")
        result[agent_id] = resolve_llm_selection(
            request.provider,
            model=request.model,
            base_url=request.base_url,
        )
    return result


def _project_path(root: Path, project_id: str) -> Path:
    checked_id = _project_id_or_422(project_id)
    path = ProjectPathRegistry(root).resolve(checked_id)
    if not path.is_dir() or not (path / "project.yaml").is_file():
        raise HTTPException(status_code=404, detail="项目不存在")
    return path

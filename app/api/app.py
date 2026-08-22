"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

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
from app.planner.service import PlannerFailure
from app.project.project import Project
from app.project.paths import ProjectPathRegistry
from app.runtime.startup import ensure_startup_scripts
from app.runtime.local_status import LocalRuntimeStatusStore


_PROJECT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str | None = Field(default=None, min_length=1, max_length=1024)


class ProjectImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1024)


class StartRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    workflow_id: str = Field(min_length=1, max_length=100)


class MemoryDecisionRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=128)


class ConversationMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class CapabilityApprovalRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=128)


def create_app(*, projects_root: str = "./projects") -> FastAPI:
    """创建 HTTP 应用，不在 import 时创建项目或发起 Agent 执行。"""
    root = Path(projects_root).resolve()
    paths = ProjectPathRegistry(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        coordinator = RunCoordinator()
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

    @app.get("/api/v1/projects/{project_id}/runtime/preflight")
    def runtime_preflight(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        return {
            "project_id": project_id,
            "sandbox": SandboxController().preflight(str(project_path)),
        }

    @app.get("/api/v1/projects/{project_id}/runtime/status")
    def runtime_status(project_id: str) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        return {
            "project_id": project_id,
            "environment": EnvironmentProvisioner().status(str(project_path)),
            "local": LocalRuntimeStatusStore().read(str(project_path)),
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
            started = service.start_controlled_workflow(
                project_path=str(project_path),
                goal=payload.goal,
                workflow_id=payload.workflow_id,
            )
        except PlannerFailure as error:
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
            action = service.send_message(
                project_path=str(project_path),
                conversation_id=conversation_id,
                content=payload.content,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ConversationBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (PlannerFailure, ValueError) as error:
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
        return {
            **trace,
            "runtime_status": coordinator.status(trace_id) or trace["status"],
        }

    @app.post(
        "/api/v1/projects/{project_id}/runs/{trace_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def resume_run(
        project_id: str, trace_id: str, request: Request,
        payload: CapabilityApprovalRequest | None = None,
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
        if waiting is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": [], "candidates": []}
        plan = traces.load_plan(trace_id)
        item = plan.work_item(str(waiting.get("work_item_id", "")))
        if item is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": [], "candidates": []}
        container = build_container(str(project_path))
        definition = container.agents.definition(item.agent_id)
        if definition is None:
            return {"trace_id": trace_id, "status": trace.get("status"), "capabilities": [], "candidates": []}
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
        }

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
        except (ApplicationRunError, FileNotFoundError, ValueError) as error:
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


def _project_path(root: Path, project_id: str) -> Path:
    checked_id = _project_id_or_422(project_id)
    path = ProjectPathRegistry(root).resolve(checked_id)
    if not path.is_dir() or not (path / "project.yaml").is_file():
        raise HTTPException(status_code=404, detail="项目不存在")
    return path

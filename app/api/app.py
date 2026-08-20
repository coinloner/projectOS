"""FastAPI 应用工厂。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.application.runs import RunCoordinator, RunService
from app.application.project_runtime import ProjectRuntimeService
from app.memory.store import MemoryStore
from app.sandbox.application_runner import ApplicationRunError
from app.orchestration.trace import TraceStore
from app.planner.service import PlannerFailure
from app.project.project import Project


_PROJECT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class StartRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    workflow_id: str = Field(min_length=1, max_length=100)


class MemoryDecisionRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=128)


def create_app(*, projects_root: str = "./projects") -> FastAPI:
    """创建 HTTP 应用，不在 import 时创建项目或发起 Agent 执行。"""
    root = Path(projects_root).resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        coordinator = RunCoordinator()
        app.state.coordinator = coordinator
        app.state.run_service = RunService(coordinator=coordinator)
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
        if Project.exists(project_id, str(root)):
            raise HTTPException(status_code=409, detail="项目已存在")
        Project(name=project_id, base_dir=str(root)).create()
        return {"project_id": project_id, "status": "created"}

    @app.get("/api/v1/projects/{project_id}/workflows")
    def list_workflows(project_id: str, request: Request) -> dict[str, object]:
        project_path = _project_path(root, project_id)
        service: RunService = request.app.state.run_service
        return {
            "project_id": project_id,
            "workflows": service.controlled_workflows(str(project_path)),
        }

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
        project_id: str, trace_id: str, request: Request
    ) -> dict[str, str]:
        project_path = _project_path(root, project_id)
        service: RunService = request.app.state.run_service
        try:
            resumed = service.resume_run(
                project_path=str(project_path), trace_id=trace_id
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
    path = (root / checked_id).resolve()
    if path.parent != root or not path.is_dir() or not (path / "project.yaml").is_file():
        raise HTTPException(status_code=404, detail="项目不存在")
    return path

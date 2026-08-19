"""构造一次 ProjectOS 运行时的组合根。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.registry import AgentRegistry
from app.artifact.repository import ArtifactRepository
from app.artifact.store import ArtifactStore
from app.memory.store import MemoryStore
from app.orchestration.runner import GraphRunner
from app.orchestration.trace import TraceStore
from app.planner.planner import CrewAIPlannerRuntime, PlannerRuntime
from app.planner.service import PlannerService
from app.tool_manager.gateway import ToolGateway
from app.workflow.template import WorkflowTemplateRegistry


@dataclass
class ProjectOSContainer:
    """一次项目运行所需的共享基础设施与应用服务。

    Container 是依赖组装结果，不承载业务判断；Agent、Tool 和 Workflow 由模块安装器
    注册，API、CLI 和后台任务都通过它取得同一组运行时对象。
    """

    project_path: str
    artifacts: ArtifactStore
    artifact_repository: ArtifactRepository
    traces: TraceStore
    memory: MemoryStore
    gateway: ToolGateway
    agents: AgentRegistry
    templates: WorkflowTemplateRegistry
    planner: PlannerService = field(init=False)
    runner: GraphRunner = field(init=False)


def build_container(
    project_path: str,
    *,
    planner_runtime: PlannerRuntime | None = None,
) -> ProjectOSContainer:
    """组装完整运行时；不创建项目目录，也不发起 LLM 请求。"""
    artifacts = ArtifactStore(project_path)
    container = ProjectOSContainer(
        project_path=project_path,
        artifacts=artifacts,
        artifact_repository=ArtifactRepository(project_path),
        traces=TraceStore(project_path),
        memory=MemoryStore(project_path),
        gateway=ToolGateway(),
        agents=AgentRegistry(),
        templates=WorkflowTemplateRegistry(),
    )

    from app.bootstrap.modules import install_modules

    install_modules(container)
    container.planner = PlannerService(
        runtime=planner_runtime or CrewAIPlannerRuntime(),
        agents=container.agents,
        templates=container.templates,
        artifacts=container.artifacts,
        traces=container.traces,
        memory=container.memory,
    )
    container.runner = GraphRunner(
        container.agents,
        container.gateway,
        traces=container.traces,
        artifacts=container.artifact_repository,
        memory=container.memory,
    )
    return container

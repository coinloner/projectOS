"""构造一次 ProjectOS 运行时的组合根。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os

from app.agent.registry import AgentRegistry
from app.artifact.repository import ArtifactRepository
from app.artifact.store import ArtifactStore
from app.memory.store import MemoryStore
from app.orchestration.runner import GraphRunner
from app.orchestration.retry import RetryPolicy
from app.orchestration.trace import TraceStore
from app.planner.planner import CrewAIPlannerRuntime, PlannerRuntime
from app.planner.service import PlannerService
from app.tool_manager.gateway import ToolGateway
from app.workflow.template import WorkflowTemplateRegistry
from app.skill.module import SkillModule
from app.policy.module import PolicyModule
from app.llm.config import LLMSelection, resolve_llm_selection


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
    skills: SkillModule = field(init=False)
    policies: PolicyModule = field(init=False)
    planner: PlannerService = field(init=False)
    runner: GraphRunner = field(init=False)
    llm_selection: LLMSelection = field(init=False)
    llm_overrides: dict[str, LLMSelection] = field(init=False)


def build_container(
    project_path: str,
    *,
    planner_runtime: PlannerRuntime | None = None,
    llm_selection: LLMSelection | None = None,
    llm_overrides: dict[str, LLMSelection] | None = None,
    retry_policy: RetryPolicy | None = None,
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
    container.llm_selection = llm_selection or resolve_llm_selection()
    container.llm_overrides = dict(llm_overrides or {})

    from app.bootstrap.modules import install_modules

    install_modules(container)
    unknown_agents = sorted(set(container.llm_overrides) - {
        definition.id for definition in container.agents.definitions()
    } - {"planner"})
    if unknown_agents:
        raise ValueError(
            "LLM 覆盖引用了未注册 Agent: " + ", ".join(unknown_agents)
        )
    planner_selection = container.llm_overrides.get("planner", container.llm_selection)
    container.planner = PlannerService(
        runtime=planner_runtime or CrewAIPlannerRuntime(llm_selection=planner_selection),
        agents=container.agents,
        templates=container.templates,
        artifacts=container.artifacts,
        traces=container.traces,
        memory=container.memory,
    )
    container.runner = GraphRunner(
        container.agents,
        container.gateway,
        max_workers=_configured_positive_int("PROJECTOS_GRAPH_MAX_WORKERS", 6),
        traces=container.traces,
        artifacts=container.artifact_repository,
        memory=container.memory,
        skills=container.skills,
        policies=container.policies,
        retry_policy=retry_policy,
        llm_selection=container.llm_selection,
        llm_overrides=container.llm_overrides,
    )
    return container


def _configured_positive_int(name: str, default: int) -> int:
    """Read a positive concurrency setting without allowing invalid values."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

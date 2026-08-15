"""Planner 可读取的受控只读上下文。"""

from __future__ import annotations

from dataclasses import dataclass
import json

from app.agent.registry import AgentRegistry
from app.artifact.store import ArtifactStore
from app.workflow.template import WorkflowTemplateRegistry
from app.workspace.store import WorkspaceStore
from app.runtime.state import RuntimeSnapshot, runtime_snapshot


@dataclass(frozen=True)
class ArtifactSnapshot:
    """Planner 只知道产物是否存在，不读取其业务内容。"""

    key: str
    filename: str
    exists: bool


@dataclass(frozen=True)
class AvailableAgent:
    """Planner 可选择的 Agent 合同，不包含工厂或工具。"""

    id: str
    domain: str
    description: str
    output_key: str


@dataclass(frozen=True)
class TemplateHint:
    """WorkflowTemplate 的只读摘要，只作为规划经验而非强制流程。"""

    id: str
    name: str
    description: str
    agent_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Planner 可见的实现状态摘要，不暴露 workspace 文件正文。"""

    implementation_file_count: int


@dataclass(frozen=True)
class PlanningContext:
    """一次规划可见的完整控制面上下文。"""

    goal: str
    artifacts: tuple[ArtifactSnapshot, ...]
    agents: tuple[AvailableAgent, ...]
    templates: tuple[TemplateHint, ...]
    workspace: WorkspaceSnapshot
    runtime: RuntimeSnapshot

    @classmethod
    def build(
        cls,
        *,
        goal: str,
        agents: AgentRegistry,
        templates: WorkflowTemplateRegistry,
        artifacts: ArtifactStore,
    ) -> PlanningContext:
        if not goal or not goal.strip():
            raise ValueError("PlanningContext.goal 不能为空")

        available_agents = tuple(
            AvailableAgent(
                id=definition.id,
                domain=definition.domain,
                description=definition.description,
                output_key=definition.output_key,
            )
            for definition in agents.definitions()
        )
        artifact_keys = tuple(
            dict.fromkeys(agent.output_key for agent in available_agents)
        )
        snapshots = tuple(
            ArtifactSnapshot(
                key=key,
                filename=ArtifactStore.filename_for(key),
                exists=artifacts.exists(key),
            )
            for key in artifact_keys
        )
        template_hints = tuple(
            TemplateHint(
                id=template.id,
                name=template.name,
                description=template.description,
                agent_ids=tuple(node.agent_id for node in template.nodes),
            )
            for template in templates.templates()
        )
        workspace = WorkspaceStore(artifacts.project_path)
        return cls(
            goal=goal.strip(),
            artifacts=snapshots,
            agents=available_agents,
            templates=template_hints,
            workspace=WorkspaceSnapshot(
                implementation_file_count=workspace.implementation_file_count()
            ),
            runtime=runtime_snapshot(artifacts.project_path),
        )

    def as_prompt_json(self) -> str:
        """稳定序列化为提供给 Planner 的纯数据。"""
        return json.dumps(
            {
                "goal": self.goal,
                "artifacts": [artifact.__dict__ for artifact in self.artifacts],
                "agents": [agent.__dict__ for agent in self.agents],
                "templates": [
                    {
                        **template.__dict__,
                        "agent_ids": list(template.agent_ids),
                    }
                    for template in self.templates
                ],
                "workspace": self.workspace.__dict__,
                "runtime": self.runtime.__dict__,
            },
            ensure_ascii=False,
            indent=2,
        )

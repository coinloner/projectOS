"""Planner 可读取的受控只读上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    max_parallel_instances: int
    artifact_key: str | None = None


@dataclass(frozen=True)
class TemplateHint:
    """WorkflowTemplate 的只读摘要，只作为规划经验而非强制流程。"""

    id: str
    name: str
    description: str
    nodes: tuple["TemplateNodeHint", ...]


@dataclass(frozen=True)
class TemplateNodeHint:
    """Planner 可见的模板节点与默认依赖。"""

    id: str
    agent_id: str
    objective: str
    depends_on: tuple[str, ...]
    execution_mode: str = "exclusive"
    output_slot: str | None = None
    publish_target: str | None = None


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
    source_templates: tuple["WorkflowTemplate", ...] = field(
        default=(), repr=False, compare=False
    )

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
                max_parallel_instances=definition.max_parallel_instances,
                artifact_key=definition.artifact_key,
            )
            for definition in agents.definitions()
        )
        artifact_keys = tuple(
            dict.fromkeys(
                agent.artifact_key or agent.output_key for agent in available_agents
            )
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
            _template_hint(template) for template in templates.templates()
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
            source_templates=templates.templates(),
        )

    def template_source(self, template_id: str | None) -> "WorkflowTemplate | None":
        if template_id is None:
            return None
        return next(
            (template for template in self.source_templates if template.id == template_id),
            None,
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
                        "nodes": [
                            {
                                **node.__dict__,
                                "depends_on": list(node.depends_on),
                            }
                            for node in template.nodes
                        ],
                    }
                    for template in self.templates
                ],
                "workspace": self.workspace.__dict__,
                "runtime": self.runtime.__dict__,
            },
            ensure_ascii=False,
            indent=2,
        )


def _template_hint(template: "WorkflowTemplate") -> TemplateHint:
    nodes_by_id = {node.id: node for node in template.nodes}
    return TemplateHint(
        id=template.id,
        name=template.name,
        description=template.description,
        nodes=tuple(
            TemplateNodeHint(
                id=node.id,
                agent_id=node.agent_id,
                objective=node.objective,
                depends_on=tuple(
                    nodes_by_id[dependency].agent_id
                    for dependency in node.depends_on
                ),
                execution_mode=node.execution_mode.value,
                output_slot=node.output_slot,
                publish_target=node.publish_target,
            )
            for node in template.nodes
        ),
    )

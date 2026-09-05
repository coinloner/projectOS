"""Planner 可读取的受控只读上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from app.agent.registry import AgentRegistry
from app.artifact.store import ArtifactStore
from app.workflow.template import WorkflowTemplateRegistry
from app.workspace.store import WorkspaceStore
from app.runtime.state import RuntimeSnapshot, runtime_snapshot
from app.domain.architecture.implementation_contract import ProjectContractStore
from app.process import default_process_registry, ProcessDefinition


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
    process_id: str = "software_delivery"


@dataclass(frozen=True)
class TemplateNodeHint:
    """Planner 可见的模板节点与默认依赖。"""

    id: str
    agent_id: str
    objective: str
    depends_on: tuple[str, ...]
    execution_mode: str = "exclusive"
    slot: str | None = None
    publish_target: str | None = None


@dataclass(frozen=True)
class ProcessHint:
    """Planner 可见的流程规则摘要，不包含具体项目节点。"""

    id: str
    name: str
    stages: tuple[str, ...]
    limits: dict[str, int]


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Planner 可见的实现状态摘要，不暴露 workspace 文件正文。"""

    implementation_file_count: int


@dataclass(frozen=True)
class ProjectContractSnapshot:
    exists: bool
    layers: tuple[str, ...] = ()
    required_test_types: tuple[str, ...] = ()
    path_mapping: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "exists": self.exists,
            "layers": list(self.layers),
            "required_test_types": list(self.required_test_types),
            "path_mapping": {key: list(value) for key, value in self.path_mapping.items()},
        }


@dataclass(frozen=True)
class PlanningContext:
    """一次规划可见的完整控制面上下文。"""

    goal: str
    artifacts: tuple[ArtifactSnapshot, ...]
    agents: tuple[AvailableAgent, ...]
    templates: tuple[TemplateHint, ...]
    workspace: WorkspaceSnapshot
    runtime: RuntimeSnapshot
    project_contract: ProjectContractSnapshot
    processes: tuple[ProcessHint, ...] = ()
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
        contract_store = ProjectContractStore(artifacts.project_path)
        if contract_store.exists():
            try:
                contract = contract_store.load()
                project_contract = ProjectContractSnapshot(
                    exists=True,
                    layers=contract.layers,
                    required_test_types=contract.required_test_types,
                    path_mapping=contract.path_mapping,
                )
            except ValueError:
                project_contract = ProjectContractSnapshot(exists=False)
        else:
            project_contract = ProjectContractSnapshot(exists=False)
        return cls(
            goal=goal.strip(),
            artifacts=snapshots,
            agents=available_agents,
            templates=template_hints,
            workspace=WorkspaceSnapshot(
                implementation_file_count=workspace.implementation_file_count()
            ),
            runtime=runtime_snapshot(artifacts.project_path),
            project_contract=project_contract,
            processes=tuple(_process_hint(process) for process in default_process_registry().processes()),
            source_templates=templates.templates(),
        )

    def template_source(self, template_id: str | None) -> "WorkflowTemplate | None":
        if template_id is None:
            return None
        return next(
            (template for template in self.source_templates if template.id == template_id),
            None,
        )

    def artifact_exists(self, key: str) -> bool:
        return any(artifact.key == key and artifact.exists for artifact in self.artifacts)

    def as_prompt_json(self) -> str:
        """稳定序列化为 Planner 可见的纯数据。

        ``WorkflowTemplate.nodes`` 是控制面内部的编译模型，不能作为 LLM
        输出协议示例暴露。这里仅提供模板候选及其依赖摘要；真正的执行
        节点、权限和发布目标仍由确定性的模板编译器生成。
        """
        return json.dumps(
            {
                "goal": self.goal,
                "artifacts": [artifact.__dict__ for artifact in self.artifacts],
                "agents": [agent.__dict__ for agent in self.agents],
                "templates": [
                    {
                        "id": template.id,
                        "name": template.name,
                        "description": template.description,
                        "process_id": template.process_id,
                        "agent_ids": [node.agent_id for node in template.nodes],
                        "default_dependency_edges": [
                            {
                                "predecessor_agent_id": predecessor,
                                "successor_agent_id": node.agent_id,
                            }
                            for node in template.nodes
                            for predecessor in node.depends_on
                        ],
                    }
                    for template in self.templates
                ],
                "workspace": self.workspace.__dict__,
                "runtime": self.runtime.__dict__,
                "project_contract": self.project_contract.as_dict(),
                "processes": [process.__dict__ for process in self.processes],
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
        process_id=template.process_id,
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
                slot=node.slot,
                publish_target=node.publish_target,
            )
            for node in template.nodes
        ),
    )


def _process_hint(process: ProcessDefinition) -> ProcessHint:
    return ProcessHint(
        id=process.id,
        name=process.name,
        stages=tuple(stage.id for stage in process.stages),
        limits={
            "max_architecture_depth": process.limits.max_architecture_depth,
            "max_modules": process.limits.max_modules,
            "max_implementation_units": process.limits.max_implementation_units,
        },
    )

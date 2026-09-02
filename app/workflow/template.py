from __future__ import annotations

from dataclasses import dataclass

from app.execution_context import ExecutionMode


@dataclass(frozen=True)
class TaskBlueprint:
    """WorkflowTemplate 中可复用的节点骨架。

    execution_mode 及其配套字段是模板作者声明的系统授权，不属于 Planner 的
    自由输出。普通 blueprint 保持 EXCLUSIVE 语义，兼容现有串行流程。
    """

    id: str
    agent_id: str
    objective: str
    output_key: str
    stage_id: str | None = None
    depends_on: tuple[str, ...] = ()
    execution_mode: ExecutionMode = ExecutionMode.EXCLUSIVE
    artifact_key: str | None = None
    input_refs: tuple[str, ...] = ()
    input_from: tuple[str, ...] = ()
    slot: str | None = None
    publish_target: str | None = None
    candidate_from: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    implementation_unit_id: str | None = None
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    owned_files: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("id", "agent_id", "objective", "output_key"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"TaskBlueprint.{field_name} 不能为空")
        for field_name in ("artifact_key", "slot", "publish_target", "candidate_from", "stage_id"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"TaskBlueprint.{field_name} 不能是空字符串")
        if self.implementation_unit_id is not None and not self.implementation_unit_id.strip():
            raise ValueError("TaskBlueprint.implementation_unit_id 不能是空字符串")
        for field_name in ("allowed_paths", "forbidden_paths", "required_paths", "owned_files", "policy_refs", "skill_refs"):
            if any(not value.strip() for value in getattr(self, field_name)):
                raise ValueError(f"TaskBlueprint.{field_name} 不能包含空字符串")
        if self.agent_id == "code_agent":
            for path in self.owned_files:
                normalized = path.replace("\\", "/").strip()
                if normalized.endswith("/") or any(token in normalized for token in ("*", "?", "[", "]")):
                    raise ValueError(
                        "TaskBlueprint.owned_files 必须是具体文件路径；目录/glob 只能用于 allowed_paths"
                    )
        if any(not value.strip() for value in self.input_refs):
            raise ValueError("TaskBlueprint.input_refs 不能包含空字符串")
        if any(not value.strip() for value in self.input_from):
            raise ValueError("TaskBlueprint.input_from 不能包含空字符串")
        if any(not value.strip() for value in self.acceptance_criteria):
            raise ValueError("TaskBlueprint.acceptance_criteria 不能包含空字符串")
        if any(not value.strip() for value in self.constraints):
            raise ValueError("TaskBlueprint.constraints 不能包含空字符串")
        if any(not value.strip() for value in self.non_goals):
            raise ValueError("TaskBlueprint.non_goals 不能包含空字符串")
        if self.agent_id == "task_agent" and self.execution_mode is ExecutionMode.EXCLUSIVE:
            raise ValueError(
                "task_agent 不支持 EXCLUSIVE 执行，必须使用受控产物工作流"
            )
        if self.execution_mode is ExecutionMode.PARTITIONED:
            if not self.slot:
                raise ValueError("PARTITIONED TaskBlueprint 必须指定 slot")
            if self.publish_target or self.candidate_from:
                raise ValueError("PARTITIONED TaskBlueprint 不能声明发布授权")
        elif self.execution_mode is ExecutionMode.INTEGRATION:
            if not self.publish_target:
                raise ValueError("INTEGRATION TaskBlueprint 必须指定 publish_target")
            if self.slot or self.candidate_from:
                raise ValueError("INTEGRATION TaskBlueprint 不能声明暂存或质量门授权")
        elif self.execution_mode is ExecutionMode.QUALITY_GATE:
            if not self.publish_target or not self.candidate_from:
                raise ValueError("QUALITY_GATE TaskBlueprint 必须指定发布目标和候选来源")
            if self.slot or self.input_from or self.input_refs:
                raise ValueError("QUALITY_GATE TaskBlueprint 不能声明普通输入或暂存 slot")
        elif any(value is not None for value in (self.slot, self.publish_target, self.candidate_from)):
            raise ValueError("EXCLUSIVE TaskBlueprint 不能声明分区、集成或质量门授权")

@dataclass(frozen=True)
class WorkflowTemplate:
    """常见任务的流程经验，不包含运行时状态或可执行对象。"""

    id: str
    name: str
    description: str
    nodes: tuple[TaskBlueprint, ...]
    # Process rules are independent from the concrete node graph.  Existing
    # templates remain valid adapters while dynamic planning is introduced.
    process_id: str = "software_delivery"

    def __post_init__(self) -> None:
        for field_name in ("id", "name", "description"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"WorkflowTemplate.{field_name} 不能为空")
        if not self.nodes:
            raise ValueError("WorkflowTemplate 至少需要一个节点")
        if not self.process_id or not self.process_id.strip():
            raise ValueError("WorkflowTemplate.process_id 不能为空")
        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("WorkflowTemplate 包含重复 Blueprint id")
        known_ids = set(node_ids)
        for node in self.nodes:
            unknown = set(node.depends_on) - known_ids
            if unknown:
                raise ValueError(
                    f"Blueprint '{node.id}' 依赖不存在的节点: {', '.join(sorted(unknown))}"
                )
            if node.id in node.depends_on:
                raise ValueError(f"Blueprint '{node.id}' 不能依赖自身")

    @property
    def has_controlled_execution(self) -> bool:
        return any(node.execution_mode is not ExecutionMode.EXCLUSIVE for node in self.nodes)

class WorkflowTemplateRegistry:
    """Planner 可查询的流程经验目录。"""

    def __init__(self) -> None:
        self._templates: dict[str, WorkflowTemplate] = {}

    def register(self, template: WorkflowTemplate) -> None:
        if template.id in self._templates:
            raise ValueError(f"WorkflowTemplate '{template.id}' 已注册")
        self._templates[template.id] = template

    def get(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def templates(self) -> tuple[WorkflowTemplate, ...]:
        return tuple(self._templates.values())

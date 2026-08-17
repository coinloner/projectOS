from __future__ import annotations

from dataclasses import dataclass

from app.workflow.plan import ExecutionPlan
from app.workflow.trace import TraceContext
from app.workflow.work_item import DependencySource, WorkItem, WorkItemDependency


@dataclass(frozen=True)
class TaskBlueprint:
    """WorkflowTemplate 中可复用的节点骨架。"""

    id: str
    agent_id: str
    objective: str
    output_key: str
    depends_on: tuple[str, ...] = ()
    policy_id: str | None = None

    def instantiate(self) -> WorkItem:
        return WorkItem(
            id=self.id,
            agent_id=self.agent_id,
            objective=self.objective,
            output_key=self.output_key,
            dependencies=tuple(
                WorkItemDependency(
                    work_item_id=dependency,
                    source=DependencySource.TEMPLATE,
                    rule_id=f"template:{dependency}->{self.id}",
                )
                for dependency in self.depends_on
            ),
            policy_id=self.policy_id,
        )


@dataclass(frozen=True)
class WorkflowTemplate:
    """常见任务的流程经验，不包含运行时状态或可执行对象。"""

    id: str
    name: str
    description: str
    nodes: tuple[TaskBlueprint, ...]

    def __post_init__(self) -> None:
        for field_name in ("id", "name", "description"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"WorkflowTemplate.{field_name} 不能为空")
        if not self.nodes:
            raise ValueError("WorkflowTemplate 至少需要一个节点")

    def instantiate(
        self,
        *,
        plan_id: str,
        goal: str,
        trace: TraceContext | None = None,
    ) -> ExecutionPlan:
        """以模板为骨架创建本次运行的独立 ExecutionPlan。"""
        return ExecutionPlan(
            id=plan_id,
            goal=goal,
            template_id=self.id,
            work_items=tuple(node.instantiate() for node in self.nodes),
            trace=trace or TraceContext.ephemeral(),
        )


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

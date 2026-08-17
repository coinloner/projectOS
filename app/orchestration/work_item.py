"""编排执行层的 WorkItem 与依赖模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DependencySource(str, Enum):
    SYSTEM = "system"
    TEMPLATE = "template"
    PLANNER = "planner"


@dataclass(frozen=True)
class WorkItemDependency:
    """一个 WorkItem 依赖另一个 WorkItem 的原因。"""

    work_item_id: str
    source: DependencySource
    rule_id: str | None = None

    def __post_init__(self) -> None:
        if not self.work_item_id or not self.work_item_id.strip():
            raise ValueError("WorkItemDependency.work_item_id 不能为空")
        if self.rule_id is not None and not self.rule_id.strip():
            raise ValueError("WorkItemDependency.rule_id 不能是空字符串")


@dataclass(frozen=True)
class WorkItem:
    """一次 Trace 中可由一个 Agent 执行的最小工作单元。"""

    id: str
    agent_id: str
    objective: str
    output_key: str
    dependencies: tuple[WorkItemDependency, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    policy_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "agent_id", "objective", "output_key"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"WorkItem.{field_name} 不能为空")
        if self.policy_id is not None and not self.policy_id.strip():
            raise ValueError("WorkItem.policy_id 不能是空字符串")
        dependency_ids = self.dependency_ids
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError(f"WorkItem '{self.id}' 包含重复依赖")
        if self.id in dependency_ids:
            raise ValueError(f"WorkItem '{self.id}' 不能依赖自身")
        if any(not criterion.strip() for criterion in self.acceptance_criteria):
            raise ValueError("WorkItem.acceptance_criteria 不能包含空字符串")

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        return tuple(dependency.work_item_id for dependency in self.dependencies)

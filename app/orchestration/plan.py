"""一次 Trace 的可执行 WorkItem 计划。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.orchestration.trace import TraceContext
from app.orchestration.work_item import WorkItem
from app.execution_context import ExecutionMode


@dataclass(frozen=True)
class ExecutionPlan:
    """一次 Trace 中可执行的 WorkItem 有向无环图。"""

    id: str
    goal: str
    work_items: tuple[WorkItem, ...]
    template_id: str | None = None
    trace: TraceContext = field(default_factory=TraceContext.ephemeral)

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("ExecutionPlan.id 不能为空")
        if not self.goal or not self.goal.strip():
            raise ValueError("ExecutionPlan.goal 不能为空")
        if not self.work_items:
            raise ValueError("ExecutionPlan 至少需要一个 WorkItem")
        if self.template_id is not None and not self.template_id.strip():
            raise ValueError("ExecutionPlan.template_id 不能是空字符串")

        item_ids = [item.id for item in self.work_items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("ExecutionPlan 包含重复 WorkItem id")

        known_ids = set(item_ids)
        for item in self.work_items:
            unknown_dependencies = set(item.dependency_ids) - known_ids
            if unknown_dependencies:
                names = ", ".join(sorted(unknown_dependencies))
                raise ValueError(
                    f"WorkItem '{item.id}' 依赖不存在的工作项: {names}"
                )
            if (
                item.execution_mode is ExecutionMode.QUALITY_GATE
                and item.candidate_from_work_item_id not in item.dependency_ids
            ):
                raise ValueError(
                    f"QUALITY_GATE WorkItem '{item.id}' 必须依赖候选来源工作项"
                )

        self._ensure_acyclic()

    def work_item(self, work_item_id: str) -> WorkItem | None:
        return next(
            (item for item in self.work_items if item.id == work_item_id), None
        )

    def root_items(self) -> tuple[WorkItem, ...]:
        return tuple(item for item in self.work_items if not item.dependencies)

    def _ensure_acyclic(self) -> None:
        dependencies = {
            item.id: set(item.dependency_ids) for item in self.work_items
        }
        resolved: set[str] = set()

        while dependencies:
            ready = {
                item_id
                for item_id, required in dependencies.items()
                if required <= resolved
            }
            if not ready:
                cycle_items = ", ".join(sorted(dependencies))
                raise ValueError(
                    f"ExecutionPlan 存在循环依赖，涉及工作项: {cycle_items}"
                )

            resolved.update(ready)
            for item_id in ready:
                del dependencies[item_id]

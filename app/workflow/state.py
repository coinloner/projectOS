from __future__ import annotations

from dataclasses import dataclass, field

from app.workflow.node_result import NodeResult, NodeStatus
from app.workflow.plan import ExecutionPlan
from app.workflow.work_item import WorkItem


@dataclass
class RunState:
    """一次 ExecutionPlan 的运行状态与已产生的文本产物。"""

    plan: ExecutionPlan
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def ready_items(self) -> tuple[WorkItem, ...]:
        """返回依赖全部成功且尚未执行的工作项，保持计划中的顺序。"""
        return tuple(
            item
            for item in self.plan.work_items
            if item.id not in self.node_results
            and all(
                self.node_results.get(dependency) is not None
                and self.node_results[dependency].status is NodeStatus.COMPLETED
                for dependency in item.dependency_ids
            )
        )

    def record(self, item: WorkItem, result: NodeResult) -> None:
        if result.node_id != item.id or result.agent_id != item.agent_id:
            raise ValueError("NodeResult 与 WorkItem 不匹配")
        if item.id in self.node_results:
            raise ValueError(f"工作项 '{item.id}' 已有运行结果")

        self.node_results[item.id] = result
        if result.status is NodeStatus.COMPLETED and result.content is not None:
            self.artifacts[item.output_key] = result.content

    def is_complete(self) -> bool:
        return len(self.node_results) == len(self.plan.work_items) and all(
            result.status is NodeStatus.COMPLETED
            for result in self.node_results.values()
        )

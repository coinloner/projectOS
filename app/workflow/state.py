from __future__ import annotations

from dataclasses import dataclass, field

from app.workflow.node_result import NodeResult, NodeStatus
from app.workflow.plan import ExecutionPlan, TaskNode


@dataclass
class RunState:
    """一次 ExecutionPlan 的运行状态与已产生的文本产物。"""

    plan: ExecutionPlan
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def ready_nodes(self) -> tuple[TaskNode, ...]:
        """返回依赖全部成功且尚未执行的节点，保持计划中的顺序。"""
        return tuple(
            node
            for node in self.plan.nodes
            if node.id not in self.node_results
            and all(
                self.node_results.get(dependency) is not None
                and self.node_results[dependency].status is NodeStatus.COMPLETED
                for dependency in node.depends_on
            )
        )

    def record(self, node: TaskNode, result: NodeResult) -> None:
        if result.node_id != node.id or result.agent_id != node.agent_id:
            raise ValueError("NodeResult 与 TaskNode 不匹配")
        if node.id in self.node_results:
            raise ValueError(f"节点 '{node.id}' 已有运行结果")

        self.node_results[node.id] = result
        if result.status is NodeStatus.COMPLETED and result.content is not None:
            self.artifacts[node.output_key] = result.content

    def is_complete(self) -> bool:
        return len(self.node_results) == len(self.plan.nodes) and all(
            result.status is NodeStatus.COMPLETED
            for result in self.node_results.values()
        )

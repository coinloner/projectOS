from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskNode:
    """一次领域 Agent 执行的纯数据描述。

    Planner 未来只能选择已注册的 agent_id 和 policy_id；TaskNode 自身不携带
    Agent、Tool 或可执行函数。
    """

    id: str
    agent_id: str
    objective: str
    output_key: str
    policy_id: str | None = None
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "agent_id",
            "objective",
            "output_key",
        ):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"TaskNode.{field_name} 不能为空")

        if self.policy_id is not None and not self.policy_id.strip():
            raise ValueError("TaskNode.policy_id 不能是空字符串")

        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"TaskNode '{self.id}' 包含重复依赖")
        if self.id in self.depends_on:
            raise ValueError(f"TaskNode '{self.id}' 不能依赖自身")


@dataclass(frozen=True)
class ExecutionPlan:
    """一次任务运行的有向无环计划图。

    它是 Planner 产出的受约束数据，不是 WorkflowTemplate，也不负责执行。
    """

    id: str
    goal: str
    nodes: tuple[TaskNode, ...]
    template_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("ExecutionPlan.id 不能为空")
        if not self.goal or not self.goal.strip():
            raise ValueError("ExecutionPlan.goal 不能为空")
        if not self.nodes:
            raise ValueError("ExecutionPlan 至少需要一个节点")
        if self.template_id is not None and not self.template_id.strip():
            raise ValueError("ExecutionPlan.template_id 不能是空字符串")

        node_ids = [node.id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("ExecutionPlan 包含重复节点 id")

        known_ids = set(node_ids)
        for node in self.nodes:
            unknown_dependencies = set(node.depends_on) - known_ids
            if unknown_dependencies:
                names = ", ".join(sorted(unknown_dependencies))
                raise ValueError(
                    f"TaskNode '{node.id}' 依赖不存在的节点: {names}"
                )

        self._ensure_acyclic()

    def node(self, node_id: str) -> TaskNode | None:
        """按 id 查节点；GraphRunner 后续用它读取计划，不修改计划。"""
        return next((node for node in self.nodes if node.id == node_id), None)

    def root_nodes(self) -> tuple[TaskNode, ...]:
        """返回没有前置依赖的节点。"""
        return tuple(node for node in self.nodes if not node.depends_on)

    def _ensure_acyclic(self) -> None:
        dependencies = {node.id: set(node.depends_on) for node in self.nodes}
        resolved: set[str] = set()

        while dependencies:
            ready = {
                node_id
                for node_id, required in dependencies.items()
                if required <= resolved
            }
            if not ready:
                cycle_nodes = ", ".join(sorted(dependencies))
                raise ValueError(
                    f"ExecutionPlan 存在循环依赖，涉及节点: {cycle_nodes}"
                )

            resolved.update(ready)
            for node_id in ready:
                del dependencies[node_id]

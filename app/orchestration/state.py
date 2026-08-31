from __future__ import annotations

from dataclasses import dataclass, field

from app.orchestration.node_result import NodeResult, NodeStatus
from app.orchestration.plan import ExecutionPlan
from app.orchestration.work_item import WorkItem


@dataclass
class RunState:
    """一次 ExecutionPlan 的运行状态与已产生的文本产物。"""

    plan: ExecutionPlan
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def as_checkpoint(self) -> dict[str, object]:
        """序列化可恢复状态；快照不是 Artifact 的事实来源。"""
        return {
            "schema_version": 1,
            "plan_id": self.plan.id,
            "trace_id": self.plan.trace.trace_id,
            "node_results": [
                result.as_dict() for result in self.node_results.values()
            ],
            "artifacts": dict(self.artifacts),
            "completed_work_items": sorted(
                item_id
                for item_id, result in self.node_results.items()
                if result.status is NodeStatus.COMPLETED
            ),
            "pending_work_items": [
                item.id
                for item in self.plan.work_items
                if item.id not in self.node_results
            ],
        }

    @classmethod
    def from_checkpoint(
        cls, plan: ExecutionPlan, checkpoint: dict[str, object]
    ) -> "RunState":
        if checkpoint.get("schema_version") != 1:
            raise ValueError("不支持的 RunState checkpoint 版本")
        if checkpoint.get("plan_id") != plan.id:
            raise ValueError("checkpoint 与 ExecutionPlan 不匹配")
        if checkpoint.get("trace_id") != plan.trace.trace_id:
            raise ValueError("checkpoint 与 Trace 不匹配")
        raw_results = checkpoint.get("node_results", [])
        if not isinstance(raw_results, list):
            raise ValueError("checkpoint.node_results 格式无效")
        results: dict[str, NodeResult] = {}
        seen_ids: set[str] = set()
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                raise ValueError("checkpoint 包含无效 NodeResult")
            result = NodeResult.from_dict(raw_result)
            item = plan.work_item(result.work_item_id)
            if item is None or item.agent_id != result.agent_id:
                raise ValueError("checkpoint 包含不属于当前计划的 NodeResult")
            if result.work_item_id in seen_ids:
                raise ValueError("checkpoint 包含重复 NodeResult")
            seen_ids.add(result.work_item_id)
            # 只有 completed 结果可以跨进程信任；失败、等待和 replan 节点恢复时
            # 必须重新执行，避免把半完成副作用误判为已完成。
            if result.status is NodeStatus.COMPLETED:
                results[result.work_item_id] = result
        raw_artifacts = checkpoint.get("artifacts", {})
        if not isinstance(raw_artifacts, dict):
            raise ValueError("checkpoint.artifacts 格式无效")
        artifacts = {str(key): str(value) for key, value in raw_artifacts.items()}
        completed_output_keys = {
            plan.work_item(item_id).output_key
            for item_id, result in results.items()
            if result.status is NodeStatus.COMPLETED
            and plan.work_item(item_id) is not None
        }
        if not set(artifacts).issubset(completed_output_keys):
            raise ValueError("checkpoint.artifacts 包含未完成节点或未知产物")
        state = cls(plan=plan, node_results=results, artifacts=artifacts)
        for item_id, result in results.items():
            if result.status is NodeStatus.COMPLETED and item_id not in state.artifacts:
                item = plan.work_item(item_id)
                if item is not None and result.content is not None:
                    state.artifacts[item.output_key] = result.content
        return state

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
        if result.work_item_id != item.id or result.agent_id != item.agent_id:
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

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
        """序列化可恢复状态；产物正文由 ArtifactRepository 唯一持有。"""
        completed = {
            item_id: result
            for item_id, result in self.node_results.items()
            if result.status is NodeStatus.COMPLETED
        }
        artifact_refs = {
            plan_item.output_key: {
                "artifact_key": plan_item.artifact_key or plan_item.output_key,
                "work_item_id": plan_item.id,
                "contract_digest": plan_item.contract_digest,
            }
            for plan_item in self.plan.work_items
            if plan_item.id in completed
        }
        return {
            "schema_version": 1,
            "plan_id": self.plan.id,
            "trace_id": self.plan.trace.trace_id,
            "node_results": [
                {
                    **result.as_dict(),
                    # Keep only a bounded summary in the checkpoint.  The
                    # authoritative content remains in ArtifactRepository.
                    "content": None,
                }
                for result in self.node_results.values()
            ],
            "artifact_refs": artifact_refs,
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
        # ``artifacts`` is the pre-migration inline-content format.  New
        # checkpoints carry only artifact_refs and reconstruct presence from
        # completed WorkItems; old checkpoints remain readable.
        raw_artifacts = checkpoint.get("artifacts", {})
        if not isinstance(raw_artifacts, dict):
            raise ValueError("checkpoint.artifacts 格式无效")
        artifacts = {str(key): str(value) for key, value in raw_artifacts.items()}
        raw_refs = checkpoint.get("artifact_refs", {})
        if raw_refs and not isinstance(raw_refs, dict):
            raise ValueError("checkpoint.artifact_refs 格式无效")
        if isinstance(raw_refs, dict):
            for output_key, raw_ref in raw_refs.items():
                if not isinstance(raw_ref, dict):
                    raise ValueError("checkpoint.artifact_refs 包含无效引用")
                work_item_id = raw_ref.get("work_item_id")
                item = plan.work_item(str(work_item_id)) if work_item_id else None
                if item is None:
                    raise ValueError("checkpoint.artifact_refs 包含不属于当前计划的 WorkItem")
                if str(output_key) != item.output_key:
                    raise ValueError("checkpoint.artifact_refs 的 output_key 与 WorkItem 不匹配")
                digest = raw_ref.get("contract_digest")
                # Historical checkpoints predate contract_digest.  They remain
                # readable, while every new checkpoint is checked strictly.
                if digest is not None and str(digest) != item.contract_digest:
                    raise ValueError(
                        f"checkpoint WorkItem 合同指纹不匹配: {item.id}"
                    )
        completed_output_keys = {
            plan.work_item(item_id).output_key
            for item_id, result in results.items()
            if result.status is NodeStatus.COMPLETED
            and plan.work_item(item_id) is not None
        }
        if not set(artifacts).issubset(completed_output_keys):
            raise ValueError("checkpoint.artifacts 包含未完成节点或未知产物")
        state = cls(plan=plan, node_results=results, artifacts=artifacts)
        for item_id in results:
            item = plan.work_item(item_id)
            if item is not None and item.output_key not in state.artifacts:
                # Presence is enough for dependency scheduling.  Consumers
                # read the actual body through the repository by ArtifactRef.
                state.artifacts[item.output_key] = ""
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

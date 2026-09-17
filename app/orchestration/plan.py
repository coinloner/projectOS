"""一次 Trace 的可执行 WorkItem 计划。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.orchestration.trace import TraceContext
from app.orchestration.work_item import WorkItem
from app.orchestration.delivery_registry import DeliveryContractRegistry
from app.execution_context import ExecutionMode


@dataclass(frozen=True)
class ExecutionPlan:
    """一次 Trace 中可执行的 WorkItem 有向无环图。"""

    id: str
    goal: str
    work_items: tuple[WorkItem, ...]
    template_id: str | None = None
    process_id: str = "software_delivery"
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
        if not self.process_id or not self.process_id.strip():
            raise ValueError("ExecutionPlan.process_id 不能为空")

        item_ids = [item.id for item in self.work_items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("ExecutionPlan 包含重复 WorkItem id")

        known_ids = set(item_ids)
        contract_errors = DeliveryContractRegistry.validate_plan(self.work_items)
        if contract_errors:
            raise ValueError(
                "ExecutionPlan 交付合同校验失败: " + "; ".join(contract_errors)
            )

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
        self._validate_artifact_edges()

    def _validate_artifact_edges(self) -> None:
        """Check producer/consumer contracts, not just individual node schemas."""
        by_id = {item.id: item for item in self.work_items}

        def ancestors(item: WorkItem) -> set[str]:
            result: set[str] = set()
            pending = list(item.dependency_ids)
            while pending:
                parent = pending.pop()
                if parent not in result:
                    result.add(parent)
                    pending.extend(by_id[parent].dependency_ids)
            return result

        def contract(item: WorkItem):
            return DeliveryContractRegistry.contract_for(
                agent_id=item.agent_id, execution_mode=item.execution_mode,
                slot=item.slot, work_item_id=item.id, stage_id=item.stage_id,
                publish_target=item.publish_target,
            )

        for item in self.work_items:
            if item.execution_mode is ExecutionMode.QUALITY_GATE:
                producer = by_id[item.candidate_from_work_item_id]
                if (producer.execution_mode is not ExecutionMode.INTEGRATION
                        or producer.publish_target != item.publish_target):
                    raise ValueError(f"QUALITY_GATE '{item.id}' 必须引用同一发布目标的 INTEGRATION 生产者")
            consumer_contract = contract(item)
            if (item.execution_mode is not ExecutionMode.INTEGRATION
                    or consumer_contract is None):
                continue
            upstream = ancestors(item)
            for ref in item.input_refs:
                if ref.layer != "staged":
                    continue
                # Historical references may intentionally belong to another run.
                if ref.trace_id != self.trace.trace_id:
                    continue
                producer = by_id.get(ref.work_item_id)
                if producer is None:
                    raise ValueError(f"集成输入 {ref.ref_id} 的生产者不在当前计划中")
                if producer.id not in upstream:
                    raise ValueError(f"集成节点 '{item.id}' 未等待输入生产者 '{producer.id}'")
                if (producer.execution_mode is not ExecutionMode.PARTITIONED
                        or producer.slot != ref.slot
                        or (producer.artifact_key or producer.output_key) != ref.artifact_key):
                    raise ValueError(f"集成输入 {ref.ref_id} 与生产者交付位置不匹配")
                producer_contract = contract(producer)
                if (producer_contract is None or producer_contract.output_artifact_kind
                        != consumer_contract.output_artifact_kind):
                    raise ValueError(f"集成节点 '{item.id}' 与生产者 '{producer.id}' 的交付协议不匹配")

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

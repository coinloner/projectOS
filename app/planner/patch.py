"""计划基线和局部 PlanPatch 的窄边界模型。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.registry import AgentRegistry
from app.execution_context import ExecutionMode
from app.orchestration.plan import ExecutionPlan
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency


class PlanPatchError(ValueError):
    """补丁不符合基线或局部修改预算。"""


class PatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["modify", "add", "remove"]
    work_item_id: str | None = Field(default=None, max_length=128)
    ref: str | None = Field(default=None, max_length=100)
    agent_id: str | None = Field(default=None, max_length=100)
    objective: str | None = Field(default=None, max_length=500)
    depends_on: list[str] = Field(default_factory=list, max_length=10)


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1_000)
    base_plan_id: str = Field(min_length=1, max_length=128)
    operations: list[PatchOperation] = Field(min_length=1, max_length=5)

    @classmethod
    def parse(cls, content: str) -> "PlanPatch":
        try:
            return cls.model_validate_json(content)
        except ValidationError as error:
            raise PlanPatchError(f"PlanPatch 不符合 JSON schema: {error}") from error


@dataclass(frozen=True)
class ChangeBudget:
    max_added: int = 2
    max_removed: int = 1
    max_modified: int = 2
    max_dependency_changes: int = 2


@dataclass(frozen=True)
class PlanBaseline:
    plan_id: str
    trace_id: str
    goal: str
    revision: int
    work_item_ids: tuple[str, ...]
    context_fingerprint: str

    @classmethod
    def from_plan(cls, plan: ExecutionPlan, *, revision: int = 1) -> "PlanBaseline":
        payload = {
            "goal": plan.goal,
            "template_id": plan.template_id,
            "work_items": [
                {
                    "id": item.id,
                    "agent_id": item.agent_id,
                    "objective": item.objective,
                    "dependencies": list(item.dependency_ids),
                }
                for item in plan.work_items
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cls(
            plan_id=plan.id,
            trace_id=plan.trace.trace_id,
            goal=plan.goal,
            revision=revision,
            work_item_ids=tuple(item.id for item in plan.work_items),
            context_fingerprint=fingerprint,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "trace_id": self.trace_id,
            "goal": self.goal,
            "revision": self.revision,
            "work_item_ids": list(self.work_item_ids),
            "context_fingerprint": self.context_fingerprint,
        }


@dataclass(frozen=True)
class AppliedPlanPatch:
    plan: ExecutionPlan
    invalidated_work_item_ids: tuple[str, ...]
    added_work_item_ids: tuple[str, ...]
    removed_work_item_ids: tuple[str, ...]
    dependency_change_count: int


def apply_patch(
    plan: ExecutionPlan,
    patch: PlanPatch,
    *,
    agents: AgentRegistry,
    completed_work_item_ids: set[str] | frozenset[str] = frozenset(),
    allow_completed_revision: bool = False,
    budget: ChangeBudget | None = None,
) -> AppliedPlanPatch:
    budget = budget or ChangeBudget()
    if patch.base_plan_id != plan.id:
        raise PlanPatchError(
            f"PlanPatch 基线不匹配: 需要 '{plan.id}'，收到 '{patch.base_plan_id}'"
        )
    if any(item.execution_mode is not ExecutionMode.EXCLUSIVE for item in plan.work_items):
        raise PlanPatchError("受控 Workflow 不能直接应用局部补丁，请创建新的受控运行")

    by_id = {item.id: item for item in plan.work_items}
    operations = patch.operations
    modified = [op for op in operations if op.operation == "modify"]
    added = [op for op in operations if op.operation == "add"]
    removed = [op for op in operations if op.operation == "remove"]
    dependency_changes = sum(
        op.depends_on != [] and op.work_item_id in by_id and tuple(op.depends_on) != by_id[op.work_item_id].dependency_ids
        for op in modified
    )
    if len(modified) > budget.max_modified:
        raise PlanPatchError("局部修改超过修改节点数量预算")
    if len(added) > budget.max_added:
        raise PlanPatchError("局部修改超过新增节点数量预算")
    if len(removed) > budget.max_removed:
        raise PlanPatchError("局部修改超过删除节点数量预算")
    if dependency_changes > budget.max_dependency_changes:
        raise PlanPatchError("局部修改超过依赖变化预算")

    referenced_ids = [op.work_item_id for op in operations if op.work_item_id]
    touched = set(referenced_ids)
    if len(touched) != len(referenced_ids):
        raise PlanPatchError("同一 WorkItem 不能在一个补丁中重复修改")
    for op in (*modified, *removed):
        if not op.work_item_id or op.work_item_id not in by_id:
            raise PlanPatchError("补丁引用了不存在的 WorkItem")
        if op.work_item_id in completed_work_item_ids and not allow_completed_revision:
            raise PlanPatchError(f"已完成 WorkItem 不可修改: {op.work_item_id}")
    for op in modified:
        if not op.objective and not op.depends_on:
            raise PlanPatchError("modify 操作至少需要 objective 或 depends_on")
        if op.objective is not None and not op.objective.strip():
            raise PlanPatchError("modify.objective 不能为空")
    add_refs = [op.ref for op in added if op.ref]
    if len(add_refs) != len(set(add_refs)):
        raise PlanPatchError("新增节点 ref 不能重复")
    remove_ids = {op.work_item_id for op in removed if op.work_item_id}
    dependents = {
        dependency: item.id
        for item in plan.work_items
        for dependency in item.dependency_ids
        if dependency in remove_ids and item.id not in remove_ids
    }
    if dependents:
        raise PlanPatchError(
            "不能删除仍被其他节点依赖的 WorkItem: " + ", ".join(sorted(dependents.values()))
        )

    next_items: list[WorkItem] = []
    invalidated: set[str] = set()
    for item in plan.work_items:
        op = next((candidate for candidate in modified if candidate.work_item_id == item.id), None)
        if item.id in remove_ids:
            invalidated.add(item.id)
            continue
        if op is None:
            next_items.append(item)
            continue
        dependencies = item.dependencies
        if op.depends_on:
            dependencies = tuple(
                WorkItemDependency(work_item_id=dependency, source=DependencySource.PLANNER)
                for dependency in op.depends_on
            )
        next_items.append(
            WorkItem(
                id=item.id,
                agent_id=item.agent_id,
                objective=op.objective or item.objective,
                output_key=item.output_key,
                artifact_key=item.artifact_key,
                failure_package=item.failure_package,
                dependencies=dependencies,
                acceptance_criteria=item.acceptance_criteria,
                constraints=item.constraints,
                non_goals=item.non_goals,
                policy_id=item.policy_id,
                execution_mode=item.execution_mode,
                input_refs=item.input_refs,
                output_slot=item.output_slot,
                publish_target=item.publish_target,
                candidate_from_work_item_id=item.candidate_from_work_item_id,
                implementation_unit_id=item.implementation_unit_id,
                allowed_paths=item.allowed_paths,
                forbidden_paths=item.forbidden_paths,
                required_paths=item.required_paths,
                wave=item.wave,
                owned_files=item.owned_files,
                policy_refs=item.policy_refs,
                skill_refs=item.skill_refs,
                requirement_ids=item.requirement_ids,
            )
        )
        invalidated.add(item.id)

    existing_ids = {item.id for item in next_items}
    for index, op in enumerate(added, start=1):
        if not op.ref or not op.agent_id or not op.objective:
            raise PlanPatchError("新增 WorkItem 必须提供 ref、agent_id 和 objective")
        definition = agents.definition(op.agent_id)
        if definition is None:
            raise PlanPatchError(f"新增节点引用未注册 Agent: {op.agent_id}")
        if any(dependency not in existing_ids for dependency in op.depends_on):
            raise PlanPatchError(f"新增节点依赖不存在: {op.ref}")
        item_id = f"wi-patch-{index:02d}-{definition.output_key}"
        if item_id in existing_ids:
            raise PlanPatchError(f"新增节点 id 冲突: {item_id}")
        next_items.append(
            WorkItem(
                id=item_id,
                agent_id=op.agent_id,
                objective=op.objective,
                output_key=f"{definition.output_key}_patch_{index:02d}",
                artifact_key=definition.artifact_key or definition.output_key,
                dependencies=tuple(
                    WorkItemDependency(work_item_id=dependency, source=DependencySource.PLANNER)
                    for dependency in op.depends_on
                ),
            )
        )
        existing_ids.add(item_id)
        invalidated.add(item_id)

    # Any downstream node depends on a changed node and must be rerun.
    changed = set(invalidated)
    while True:
        descendants = {
            item.id
            for item in next_items
            if item.id not in changed and any(dependency in changed for dependency in item.dependency_ids)
        }
        if not descendants:
            break
        changed.update(descendants)
    invalidated = changed
    patched = ExecutionPlan(
        id=f"{plan.id}-patch-{len(operations)}",
        goal=plan.goal,
        work_items=tuple(next_items),
        template_id=plan.template_id,
        trace=plan.trace,
    )
    return AppliedPlanPatch(
        plan=patched,
        invalidated_work_item_ids=tuple(sorted(invalidated)),
        added_work_item_ids=tuple(
            item.id for item in next_items if item.id.startswith("wi-patch-")
        ),
        removed_work_item_ids=tuple(sorted(remove_ids)),
        dependency_change_count=dependency_changes,
    )

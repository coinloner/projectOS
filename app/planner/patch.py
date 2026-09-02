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


class RepairPlanPatch(BaseModel):
    """失败恢复专用的局部计划补丁。

    与普通 ``PlanPatch`` 不同，它只描述要追加执行的修复 WorkItem，绝不
    重新生成原交付 DAG。历史响应中的 ``steps`` 仅在解析边界兼容，内部
    一律转换为 ``operations``。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1_000)
    schema_version: int = Field(default=1, ge=1, le=1)
    base_plan_id: str = Field(min_length=1, max_length=128)
    repair_scope: list[str] = Field(default_factory=list, max_length=10)
    # Diagnostic metadata is advisory but structured so retries can be
    # audited without scraping natural-language rationale.
    failure_kind: str | None = Field(default=None, min_length=1, max_length=64)
    verification: list[str] = Field(default_factory=list, max_length=10)
    operations: list[PatchOperation] = Field(min_length=1, max_length=10)

    @classmethod
    def parse(
        cls,
        content: str,
        *,
        base_plan_id: str | None = None,
        repair_scope: tuple[str, ...] = (),
    ) -> "RepairPlanPatch":
        try:
            raw = json.loads(content)
        except (TypeError, json.JSONDecodeError) as error:
            raise PlanPatchError(f"RepairPlanPatch 不是合法 JSON: {error}") from error
        if not isinstance(raw, dict):
            raise PlanPatchError("RepairPlanPatch 顶层必须是对象")
        if base_plan_id and not raw.get("base_plan_id"):
            raw["base_plan_id"] = base_plan_id
        if repair_scope and not raw.get("repair_scope"):
            raw["repair_scope"] = list(repair_scope)
        # Canonicalize legacy planner responses that omitted verification.
        # The control plane still requires a concrete evidence-producing step;
        # this default keeps old checkpoints readable while making the
        # requirement explicit in the persisted patch.
        raw.setdefault("verification", ["由控制面重新运行失败检查并产生新证据"])
        try:
            patch = cls.model_validate(raw)
        except ValidationError as error:
            raise PlanPatchError(f"RepairPlanPatch 不符合 JSON schema: {error}") from error
        if any(operation.operation != "add" for operation in patch.operations):
            raise PlanPatchError("RepairPlanPatch 只允许追加修复 WorkItem")
        if repair_scope and set(patch.repair_scope) != set(repair_scope):
            raise PlanPatchError("RepairPlanPatch.repair_scope 必须与控制面修复窗口完全一致")
        if any(not step.strip() for step in patch.verification):
            raise PlanPatchError("RepairPlanPatch.verification 不能包含空步骤")
        return patch


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1_000)
    schema_version: int = Field(default=1, ge=1, le=1)
    base_plan_id: str = Field(min_length=1, max_length=128)
    operations: list[PatchOperation] = Field(min_length=1, max_length=5)
    # Optional audit metadata emitted by repair-aware callers.  The actual
    # scope is still checked against the plan graph; this field is never an
    # authority grant by itself.
    repair_scope: list[str] = Field(default_factory=list, max_length=10)

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
    contract_digests: dict[str, str]

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
                    "contract_digest": item.contract_digest,
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
            contract_digests={item.id: item.contract_digest or "" for item in plan.work_items},
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "trace_id": self.trace_id,
            "goal": self.goal,
            "revision": self.revision,
            "work_item_ids": list(self.work_item_ids),
            "context_fingerprint": self.context_fingerprint,
            "contract_digests": dict(self.contract_digests),
        }


@dataclass(frozen=True)
class AppliedPlanPatch:
    plan: ExecutionPlan
    invalidated_work_item_ids: tuple[str, ...]
    added_work_item_ids: tuple[str, ...]
    removed_work_item_ids: tuple[str, ...]
    dependency_change_count: int


def apply_repair_patch(
    plan: ExecutionPlan,
    patch: RepairPlanPatch,
    *,
    agents: AgentRegistry,
    plan_id: str | None = None,
) -> ExecutionPlan:
    """Append bounded repair WorkItems to an existing plan.

    The original DAG and all existing WorkItems remain untouched.  Repair
    operations may depend on original WorkItem ids or on an earlier repair
    ``ref`` in the same patch, but cannot introduce an unknown dependency.
    """
    if patch.base_plan_id != plan.id:
        raise PlanPatchError(
            f"RepairPlanPatch 基线不匹配: 需要 '{plan.id}'，收到 '{patch.base_plan_id}'"
        )
    original_ids = {item.id for item in plan.work_items}
    if patch.repair_scope and not set(patch.repair_scope).issubset(original_ids):
        raise PlanPatchError("RepairPlanPatch repair_scope 包含未知 WorkItem")
    refs: set[str] = set()
    ref_to_id: dict[str, str] = {}
    additions: list[WorkItem] = []
    for index, operation in enumerate(patch.operations, start=1):
        if operation.operation != "add" or not operation.ref or not operation.agent_id or not operation.objective:
            raise PlanPatchError("修复补丁的每个操作必须是完整的 add WorkItem")
        if operation.ref in refs:
            raise PlanPatchError(f"修复补丁 ref 重复: {operation.ref}")
        definition = agents.definition(operation.agent_id)
        if definition is None:
            raise PlanPatchError(f"修复补丁引用未注册 Agent: {operation.agent_id}")
        dependencies: list[WorkItemDependency] = []
        for dependency in operation.depends_on:
            dependency_id = ref_to_id.get(dependency, dependency)
            if dependency_id not in {item.id for item in additions}:
                raise PlanPatchError(
                    f"修复节点 depends_on 只能引用同一补丁中更早的 ref: {dependency}"
                )
            dependencies.append(
                WorkItemDependency(dependency_id, DependencySource.PLANNER)
            )
        item_id = f"wi-repair-{index:02d}-{definition.output_key}"
        if item_id in original_ids or item_id in {item.id for item in additions}:
            raise PlanPatchError(f"修复节点 id 冲突: {item_id}")
        additions.append(
            WorkItem(
                id=item_id,
                agent_id=operation.agent_id,
                objective=operation.objective,
                output_key=f"{definition.output_key}_repair_{index:02d}",
                artifact_key=definition.artifact_key or definition.output_key,
                dependencies=tuple(dependencies),
            )
        )
        refs.add(operation.ref)
        ref_to_id[operation.ref] = item_id
    return ExecutionPlan(
        id=plan_id or f"{plan.id}-repair",
        goal=plan.goal,
        work_items=tuple(additions),
        template_id=None,
        trace=plan.trace,
    )


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

    # A persisted plan may have been tampered with between attempts.  Rebuild
    # validation in WorkItem.__post_init__ catches malformed digests; this
    # explicit check keeps the failure at the patch boundary with a useful
    # control-plane error instead of letting a Worker start with a different
    # authorization envelope.
    for item in plan.work_items:
        if item.contract_digest != item._compute_contract_digest():
            raise PlanPatchError(f"WorkItem 合同指纹无效: {item.id}")

    by_id = {item.id: item for item in plan.work_items}
    operations = patch.operations
    if patch.repair_scope:
        scope = set(patch.repair_scope)
        unknown_scope = scope - set(by_id)
        if unknown_scope:
            raise PlanPatchError(
                "repair_scope 引用了不存在的 WorkItem: " + ", ".join(sorted(unknown_scope))
            )
        out_of_scope = {
            op.work_item_id
            for op in operations
            if op.work_item_id and op.work_item_id not in scope
        }
        if out_of_scope:
            raise PlanPatchError(
                "PlanPatch 超出 repair_scope: " + ", ".join(sorted(out_of_scope))
            )
        out_of_scope_dependencies = {
            dependency
            for op in operations
            if op.depends_on
            for dependency in op.depends_on
            if dependency not in scope
        }
        if out_of_scope_dependencies:
            raise PlanPatchError(
                "PlanPatch 依赖超出 repair_scope: "
                + ", ".join(sorted(out_of_scope_dependencies))
            )
    modified = [op for op in operations if op.operation == "modify"]
    added = [op for op in operations if op.operation == "add"]
    removed = [op for op in operations if op.operation == "remove"]
    dependency_changes = sum(
        op.depends_on != [] and op.work_item_id in by_id and tuple(op.depends_on) != by_id[op.work_item_id].dependency_ids
        for op in modified
    )
    if dependency_changes:
        raise PlanPatchError("WorkItem 合同已冻结，局部补丁不能修改 dependencies")
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
        if not op.objective:
            raise PlanPatchError("modify 操作必须提供 objective")
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
        next_items.append(
            WorkItem(
                id=item.id,
                agent_id=item.agent_id,
                objective=op.objective or item.objective,
                output_key=item.output_key,
                artifact_key=item.artifact_key,
                failure_package=item.failure_package,
                dependencies=item.dependencies,
                acceptance_criteria=item.acceptance_criteria,
                constraints=item.constraints,
                non_goals=item.non_goals,
                policy_refs=item.policy_refs,
                execution_mode=item.execution_mode,
                input_refs=item.input_refs,
                slot=item.slot,
                publish_target=item.publish_target,
                candidate_from_work_item_id=item.candidate_from_work_item_id,
                implementation_unit_id=item.implementation_unit_id,
                allowed_paths=item.allowed_paths,
                forbidden_paths=item.forbidden_paths,
                required_paths=item.required_paths,
                wave=item.wave,
                owned_files=item.owned_files,
                skill_refs=item.skill_refs,
                requirement_ids=item.requirement_ids,
                delivery_contract=item.delivery_contract,
                output_kind=item.output_kind,
                contract_digest=item.contract_digest,
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

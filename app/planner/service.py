"""Planner v0 服务：构造上下文、请求草案、校验并执行一次修复。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json

from app.agent.registry import AgentRegistry
from app.artifact.store import ArtifactStore
from app.memory.store import MemoryStore
from app.memory.context import MemoryContextAssembler
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft, PlanDraftError
from app.planner.errors import PlanValidationError
from app.planner.planner import PlannerRuntime
from app.planner.validator import PlanValidator
from app.planner.patch import (
    AppliedPlanPatch,
    PlanPatch,
    PlanPatchError,
    apply_patch,
)
from app.workflow.template import WorkflowTemplateRegistry
from app.orchestration.plan import ExecutionPlan
from app.orchestration.retry import FailurePackage, FailureSignal
from app.orchestration.trace import TraceStore


@dataclass(frozen=True)
class PlannerResult:
    """一次成功规划的可展示结果。"""

    plan: ExecutionPlan
    draft: PlanDraft
    context: PlanningContext
    attempts: int


class PlannerFailure(RuntimeError):
    """Planner 在一次修复后仍无法产出合法计划。"""


class PlannerService:
    """ProjectOS 的计划入口，不向 Planner 暴露可执行对象。"""

    def __init__(
        self,
        *,
        runtime: PlannerRuntime,
        agents: AgentRegistry,
        templates: WorkflowTemplateRegistry,
        artifacts: ArtifactStore,
        validator: PlanValidator | None = None,
        traces: TraceStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self._runtime = runtime
        self._agents = agents
        self._templates = templates
        self._artifacts = artifacts
        self._validator = validator or PlanValidator()
        self._traces = traces or TraceStore(artifacts.project_path)
        self._memory = memory or MemoryStore(artifacts.project_path)
        self._memory_context = MemoryContextAssembler(self._memory)

    def plan(self, *, goal: str, plan_id: str) -> PlannerResult:
        goal = _require_goal(goal)
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        prompt = _planning_prompt(context)
        trace = self._traces.start_trace(goal)
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal,
        )

        for attempt in (1, 2):
            self._memory.append(
                trace_id=trace.trace_id,
                role="system",
                event_type="planner_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            raw_draft = self._runtime.generate(prompt)
            self._memory.append(
                trace_id=trace.trace_id,
                role="planner",
                event_type="draft_output",
                content=raw_draft,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                draft = PlanDraft.parse(raw_draft)
                plan = self._validator.validate(
                    draft,
                    context=context,
                    plan_id=plan_id,
                    trace=trace,
                )
                return PlannerResult(
                    plan=plan,
                    draft=draft,
                    context=context,
                    attempts=attempt,
                )
            except (PlanDraftError, PlanValidationError) as error:
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在修复后仍无法生成合法计划: {error}"
                    ) from error
                prompt = _repair_prompt(context, raw_draft, str(error))

        raise AssertionError("Planner 修复循环未按预期结束")

    def plan_controlled_workflow(
        self, *, goal: str, plan_id: str, workflow_id: str
    ) -> PlannerResult:
        """直接编译一个由调用方明确选择的受控 Workflow。

        API/CLI 可以选择已注册的流程模板，但不能提交其中的 WorkItem 授权字段。
        这类模板不需要 LLM 再次拆解节点，避免把 slot、发布目标等控制面权限交给模型。
        """
        goal = _require_goal(goal)
        template = self._templates.get(workflow_id)
        if template is None:
            raise PlannerFailure(f"未注册 Workflow: '{workflow_id}'")
        if not template.has_controlled_execution:
            raise PlannerFailure(
                f"Workflow '{workflow_id}' 不是受控执行模板，不能由该入口直接运行"
            )
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        trace = self._traces.start_trace(goal)
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal,
        )
        draft = PlanDraft.model_validate(
            {
                "rationale": f"调用方明确选择受控 Workflow: {workflow_id}",
                "template_hint_id": workflow_id,
                "steps": [],
            }
        )
        try:
            plan = self._validator.validate(
                draft,
                context=context,
                plan_id=plan_id,
                trace=trace,
            )
        except PlanValidationError as error:
            raise PlannerFailure(f"受控 Workflow 无法生成计划: {error}") from error
        self._memory.append(
            trace_id=trace.trace_id,
            role="system",
            event_type="planner_input",
            content=f"controlled_workflow={workflow_id}",
            attempt=1,
            metadata={"plan_id": plan_id, "controlled": True},
        )
        self._memory.append(
            trace_id=trace.trace_id,
            role="planner",
            event_type="draft_output",
            content=draft.model_dump_json(),
            attempt=1,
            metadata={"plan_id": plan_id, "controlled": True},
        )
        return PlannerResult(
            plan=plan,
            draft=draft,
            context=context,
            attempts=0,
        )

    def plan_patch(
        self,
        *,
        previous_plan: ExecutionPlan,
        change_request: str,
        completed_work_item_ids: set[str] | frozenset[str] = frozenset(),
        allow_completed_revision: bool = False,
        plan_id: str | None = None,
    ) -> AppliedPlanPatch:
        """只为既有计划生成局部补丁，不重新规划整张 DAG。"""
        if not isinstance(change_request, str) or not change_request.strip():
            raise PlannerFailure("修改请求不能为空")
        patch_plan_id = plan_id or f"{previous_plan.id}-patch"
        prompt = _patch_prompt(
            previous_plan,
            change_request.strip(),
            completed_work_item_ids,
        )
        for attempt in (1, 2):
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="system",
                event_type="planner_patch_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": patch_plan_id},
            )
            raw_patch = self._runtime.generate(prompt)
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="planner",
                event_type="patch_output",
                content=raw_patch,
                attempt=attempt,
                metadata={"plan_id": patch_plan_id},
            )
            try:
                patch = PlanPatch.parse(raw_patch)
                return apply_patch(
                    previous_plan,
                    patch,
                    agents=self._agents,
                    completed_work_item_ids=completed_work_item_ids,
                    allow_completed_revision=allow_completed_revision,
                )
            except PlanPatchError as error:
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在局部修改后仍无法生成合法补丁: {error}"
                    ) from error
                prompt = _patch_repair_prompt(prompt, raw_patch, str(error))
        raise AssertionError("Planner patch 修复循环未按预期结束")

    def plan_repair(
        self,
        *,
        previous_plan: ExecutionPlan,
        failure: FailureSignal,
        plan_id: str,
        repair_scope: tuple[str, ...] = (),
    ) -> PlannerResult:
        """为可信失败信号追加一段新计划，不修改已经执行过的 WorkItem。"""
        context = PlanningContext.build(
            goal=previous_plan.goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        package = self._traces.failure_package(previous_plan.trace, failure)
        package = replace(package, repair_scope=repair_scope)
        prompt = _repair_planning_prompt(
            context,
            previous_plan,
            package,
            repair_scope=repair_scope,
            memory_context=self._memory_context.build(
                trace_id=previous_plan.trace.trace_id,
                work_item_id=None,
                query=f"{previous_plan.goal} {failure.summary}",
                include_durable=True,
            ).as_prompt(),
        )
        for attempt in (1, 2):
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="system",
                event_type="planner_repair_input",
                content=prompt,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            raw_draft = self._runtime.generate(prompt)
            self._memory.append(
                trace_id=previous_plan.trace.trace_id,
                role="planner",
                event_type="repair_draft_output",
                content=raw_draft,
                attempt=attempt,
                metadata={"plan_id": plan_id},
            )
            try:
                draft = PlanDraft.parse(raw_draft)
                plan = self._validator.validate(
                    draft,
                    context=context,
                    plan_id=plan_id,
                    trace=previous_plan.trace,
                )
                # Repair plans are append-only patches, never a fresh delivery
                # workflow.  Integration/review/bootstrap agents cannot repair
                # a failed test signal and their default EXCLUSIVE mode would
                # violate their execution contracts.  Reject such drafts before
                # they reach the Worker so the second planner attempt can fix
                # the scope instead of producing a deterministic runtime error.
                repair_domains = {
                    self._agents.definition(item.agent_id).domain
                    for item in plan.work_items
                    if self._agents.definition(item.agent_id) is not None
                }
                # bootstrap is permitted for environment/setup failures; the
                # repair plan still must never recreate integration/review or
                # planning nodes as generic EXCLUSIVE tasks.
                invalid_domains = repair_domains - {"bootstrap", "code", "test"}
                if invalid_domains:
                    invalid = ", ".join(sorted(invalid_domains))
                    raise PlanValidationError(
                        "修复计划不能包含 integration/review/planning Agent: " + invalid
                    )
                plan = self._attach_failure_package(plan, package)
                return PlannerResult(
                    plan=plan,
                    draft=draft,
                    context=context,
                    attempts=attempt,
                )
            except (PlanDraftError, PlanValidationError) as error:
                if attempt == 2:
                    raise PlannerFailure(
                        f"Planner 在修复计划后仍无法生成合法计划: {error}"
                    ) from error
                # 修复规划的重试必须继续携带局部修复约束。普通的
                # ``_repair_prompt`` 只适合初始计划，会丢失“禁止 review /
                # integration”等控制面边界，导致模型在第二次尝试再次生成
                # 无法执行的交付节点。
                prompt = _repair_planning_retry_prompt(
                    context,
                    previous_plan,
                    package,
                    raw_draft,
                    str(error),
                    repair_scope=repair_scope,
                    memory_context=self._memory_context.build(
                        trace_id=previous_plan.trace.trace_id,
                        work_item_id=None,
                        query=f"{previous_plan.goal} {failure.summary}",
                        include_durable=True,
                    ).as_prompt(),
                )
        raise AssertionError("Planner repair 循环未按预期结束")

    def _attach_failure_package(
        self, plan: ExecutionPlan, package: FailurePackage
    ) -> ExecutionPlan:
        work_items = []
        for item in plan.work_items:
            definition = self._agents.definition(item.agent_id)
            if definition is None or definition.domain not in {"code", "test"}:
                work_items.append(item)
                continue
            repair_paths = package.repair_paths or item.allowed_paths or item.required_paths
            forbidden_rework = package.forbidden_rework or item.forbidden_paths
            # EXCLUSIVE CodeAgent repairs must never touch tests or control-plane
            # metadata. Keep this deterministic even when the Planner omits paths.
            if definition.domain == "code":
                forbidden_rework = tuple(dict.fromkeys((*forbidden_rework,
                    "tests/**", ".projectos/**", "project.yaml", "runtime.yaml")))
            scoped = replace(
                package,
                repair_paths=repair_paths,
                forbidden_rework=forbidden_rework,
                unsatisfied_constraints=item.acceptance_criteria,
                satisfied_constraints=tuple(
                    criterion for criterion in item.constraints
                    if criterion not in item.acceptance_criteria
                ),
                owner_files=item.owned_files or item.required_paths,
            )
            if definition.domain == "code" and repair_paths:
                item = replace(
                    item,
                    allowed_paths=tuple(repair_paths),
                    forbidden_paths=tuple(forbidden_rework),
                    # Path scope is deterministically narrowed/expanded by
                    # the control plane from FailurePackage evidence. Seal a
                    # fresh digest for this repaired contract.
                    contract_digest=None,
                )
            work_items.append(replace(item, failure_package=scoped))
        return replace(plan, work_items=work_items)


def _require_goal(goal: str) -> str:
    if not isinstance(goal, str) or not goal.strip():
        raise PlannerFailure("Planner goal 不能为空")
    return goal.strip()


def _patch_prompt(
    plan: ExecutionPlan,
    change_request: str,
    completed_work_item_ids: set[str] | frozenset[str],
) -> str:
    items = [
        {
            "id": item.id,
            "agent_id": item.agent_id,
            "objective": item.objective,
            "depends_on": list(item.dependency_ids),
        }
        for item in plan.work_items
    ]
    return (
        "请只为现有计划生成 PlanPatch JSON，不要重新生成完整计划。\n\n"
        f"base_plan_id: {plan.id}\n"
        f"用户修改请求：{change_request}\n"
        f"已完成 WorkItem（历史结果不可覆写，但本次补丁可创建新 revision 重新计算）：{sorted(completed_work_item_ids)}\n"
        f"现有 WorkItem：{json.dumps(items, ensure_ascii=False)}\n\n"
        "只允许 operation=modify/add/remove。modify 只能修改 objective 或显式 depends_on；"
        "add 必须提供 ref、agent_id、objective 和 depends_on；remove 必须提供 work_item_id。"
        "不允许改变 Agent 权限、execution_mode、artifact、slot、发布目标或质量门。"
        "最多 modify 2 个、add 2 个、remove 1 个节点；只输出 JSON。\n"
        '{"rationale":"...","base_plan_id":"...","operations":['
        '{"operation":"modify","work_item_id":"...","objective":"..."}]}'
    )


def _patch_repair_prompt(previous_prompt: str, raw_patch: str, error: str) -> str:
    return (
        "上一份 PlanPatch 无法通过确定性边界校验。请只输出修正后的 PlanPatch JSON。\n\n"
        f"原始约束：\n{previous_prompt}\n\n"
        f"上一份补丁：\n{raw_patch}\n\n"
        f"校验错误：\n{error}\n"
    )


def _planning_prompt(context: PlanningContext) -> str:
    return (
        "请为以下 ProjectOS 上下文生成 PlanDraft JSON。\n\n"
        "上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        "只输出 PlanDraft JSON，不要输出 Markdown 或解释。"
    )


def _repair_prompt(context: PlanningContext, invalid_draft: str, error: str) -> str:
    return (
        "上一份 PlanDraft 无法执行。请保留原目标并仅输出一份修正后的 PlanDraft JSON。\n\n"
        f"规划上下文：\n{context.as_prompt_json()}\n\n"
        f"上一份草案：\n{invalid_draft}\n\n"
        f"校验错误：\n{error}\n\n"
        "不要输出 Markdown 或解释。"
    )


def _repair_planning_prompt(
    context: PlanningContext,
    previous_plan: ExecutionPlan,
    package: FailurePackage,
    *,
    repair_scope: tuple[str, ...] = (),
    memory_context: str = "",
) -> str:
    previous_steps = [
        {
            "work_item_id": item.id,
            "agent_id": item.agent_id,
            "objective": item.objective,
        }
        for item in previous_plan.work_items
    ]
    scope_text = (
        "本次允许影响的局部节点：" + ", ".join(repair_scope) + "\n\n"
        if repair_scope else ""
    )
    return (
        "请为一次受控失败生成追加 PlanDraft JSON。\n\n"
        "失败信号（可信控制面数据）：\n"
        f"{json.dumps(package.as_planner_data(), ensure_ascii=False)}\n\n"
        "历史计划摘要（只读，不能修改或复用其 WorkItem id）：\n"
        f"{previous_steps!r}\n\n"
        + scope_text
        + "当前可用控制面上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        + (f"历史会话记忆：\n{memory_context}\n\n" if memory_context else "")
        + "只输出新的 PlanDraft JSON。新步骤只能使用已注册 Agent，目标必须针对失败"
        + "进行修复或再验证；不要创建工具、修改权限、复用历史 step ref，或编写业务代码。"
        + "修复计划只允许 code_agent、test_agent；只有 sandbox/environment 故障时才可加入 "
        + "bootstrap_agent。禁止 code_integration_agent、review_agent、architecture_agent 和 task_agent。"
        + "修复步骤必须产生实际变更：code_agent 的修复步骤必须在 objective 中明确要求"
        + "通过 write_workspace_file 实际修改 workspace 文件；test_agent 的修复步骤必须"
        + "通过 write_test_file 修改测试。只读诊断不构成修复步骤。"
        + "每个 steps 项的 acceptance_criteria 最多 5 条、constraints 最多 8 条、"
        + "non_goals 最多 8 条；steps 总数最多 10 个。"
    )


def _repair_planning_retry_prompt(
    context: PlanningContext,
    previous_plan: ExecutionPlan,
    package: FailurePackage,
    invalid_draft: str,
    error: str,
    *,
    repair_scope: tuple[str, ...] = (),
    memory_context: str = "",
) -> str:
    """保留修复边界的第二次 Planner 提示。

    修复计划不是普通的动态规划：它只能追加局部的环境、代码和测试步骤。
    重试时把上一份草案和控制面校验错误作为诊断输入，但重新声明完整的
    修复协议，避免模型因通用纠错提示而重新加入 review/integration 节点。
    """
    return (
        _repair_planning_prompt(
            context,
            previous_plan,
            package,
            repair_scope=repair_scope,
            memory_context=memory_context,
        )
        + "\n\n上一份修复草案（不可信，仅用于定位校验错误）：\n"
        + invalid_draft
        + "\n\n控制面校验错误：\n"
        + error
        + "\n\n请重新输出一份满足上述修复协议和 PlanDraft 数量上限的 JSON；"
        + "每个步骤 acceptance_criteria 不超过 5 条、constraints 不超过 8 条、"
        + "non_goals 不超过 8 条；不要保留被拒绝的 Agent。"
    )

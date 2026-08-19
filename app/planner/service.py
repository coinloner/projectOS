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
        context = PlanningContext.build(
            goal=goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        prompt = _planning_prompt(context)
        trace = self._traces.start_trace(goal.strip())
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal.strip(),
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
        trace = self._traces.start_trace(goal.strip())
        self._memory.append(
            trace_id=trace.trace_id,
            role="user",
            event_type="goal",
            content=goal.strip(),
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
        return PlannerResult(
            plan=plan,
            draft=draft,
            context=context,
            attempts=0,
        )

    def plan_repair(
        self,
        *,
        previous_plan: ExecutionPlan,
        failure: FailureSignal,
        plan_id: str,
    ) -> PlannerResult:
        """为可信失败信号追加一段新计划，不修改已经执行过的 WorkItem。"""
        context = PlanningContext.build(
            goal=previous_plan.goal,
            agents=self._agents,
            templates=self._templates,
            artifacts=self._artifacts,
        )
        package = self._traces.failure_package(previous_plan.trace, failure)
        prompt = _repair_planning_prompt(
            context,
            previous_plan,
            package,
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
                prompt = _repair_prompt(context, raw_draft, str(error))
        raise AssertionError("Planner repair 循环未按预期结束")

    def _attach_failure_package(
        self, plan: ExecutionPlan, package: FailurePackage
    ) -> ExecutionPlan:
        work_items = tuple(
            replace(item, failure_package=package)
            if (definition := self._agents.definition(item.agent_id)) is not None
            and definition.domain in {"code", "test"}
            else item
            for item in plan.work_items
        )
        return replace(plan, work_items=work_items)


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
    return (
        "请为一次受控失败生成追加 PlanDraft JSON。\n\n"
        "失败信号（可信控制面数据）：\n"
        f"{json.dumps(package.as_planner_data(), ensure_ascii=False)}\n\n"
        "历史计划摘要（只读，不能修改或复用其 WorkItem id）：\n"
        f"{previous_steps!r}\n\n"
        "当前可用控制面上下文：\n"
        f"{context.as_prompt_json()}\n\n"
        + (f"历史会话记忆：\n{memory_context}\n\n" if memory_context else "")
        + "只输出新的 PlanDraft JSON。新步骤只能使用已注册 Agent，目标必须针对失败"
        + "进行修复或再验证；不要创建工具、修改权限、复用历史 step ref，或编写业务代码。"
    )

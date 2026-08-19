"""WorkItem 执行时交给 Agent 的结构化任务输入包。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.work_item import WorkItem

if TYPE_CHECKING:
    from app.orchestration.runner import RunState


@dataclass(frozen=True)
class InputBinding:
    """一个输入引用的用途说明，不包含正文。"""

    ref_id: str
    artifact_key: str
    layer: str
    purpose: str
    required: bool = True

    @classmethod
    def from_ref(cls, ref: ArtifactRef) -> "InputBinding":
        return cls(
            ref_id=ref.ref_id,
            artifact_key=ref.artifact_key,
            layer=ref.layer,
            purpose=_input_purpose(ref),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "ref_id": self.ref_id,
            "artifact_key": self.artifact_key,
            "layer": self.layer,
            "purpose": self.purpose,
            "required": self.required,
        }


@dataclass(frozen=True)
class TaskScope:
    """当前节点的资源边界，明确允许范围和非目标范围。"""

    execution_mode: str
    output_slot: str | None
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "execution_mode": self.execution_mode,
            "output_slot": self.output_slot,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
        }


@dataclass(frozen=True)
class OutputContract:
    """节点完成后必须形成的交付对象。"""

    output_key: str
    artifact_key: str
    output_slot: str | None
    publish_target: str | None
    expected_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "output_key": self.output_key,
            "artifact_key": self.artifact_key,
            "output_slot": self.output_slot,
            "publish_target": self.publish_target,
            "expected_paths": list(self.expected_paths),
        }


@dataclass(frozen=True)
class DependencySummary:
    """前置 WorkItem 的最小状态摘要，不复制前置产物正文。"""

    work_item_id: str
    agent_id: str
    artifact_key: str
    status: str
    output_available: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "work_item_id": self.work_item_id,
            "agent_id": self.agent_id,
            "artifact_key": self.artifact_key,
            "status": self.status,
            "output_available": self.output_available,
        }


@dataclass(frozen=True)
class TaskInputPackage:
    """一次 Agent 执行的完整输入合同。

    该对象只表达控制面授权和任务边界。业务正文仍由 Agent 按 InputBinding
    中的 ref_id 调用对应 Tool 读取，避免 Runner 把大段上游文档重复塞进 prompt。
    """

    trace_id: str
    work_item_id: str
    agent_id: str
    project_goal: str
    objective: str
    execution_mode: str
    scope: TaskScope
    inputs: tuple[InputBinding, ...]
    dependencies: tuple[DependencySummary, ...]
    output: OutputContract
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    non_goals: tuple[str, ...]
    failure_context: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "trace_id": self.trace_id,
            "work_item_id": self.work_item_id,
            "agent_id": self.agent_id,
            "project_goal": self.project_goal,
            "objective": self.objective,
            "execution_mode": self.execution_mode,
            "scope": self.scope.as_dict(),
            "inputs": [item.as_dict() for item in self.inputs],
            "dependencies": [item.as_dict() for item in self.dependencies],
            "output": self.output.as_dict(),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
        }
        if self.failure_context is not None:
            payload["failure_context"] = self.failure_context
        return payload

    def as_prompt(self, *, memory_context: str = "") -> str:
        summary = [
            f"总体目标：{self.project_goal}",
            f"当前工作项：{self.work_item_id}",
            f"当前任务：{self.objective}",
        ]
        if self.dependencies:
            summary.append("可用前置产物（按需读取正文，不在任务输入中展开）：")
            summary.extend(f"- {item.artifact_key}" for item in self.dependencies)
        if self.inputs:
            summary.append("可读取的授权引用（只能使用列出的 ref_id）：")
            summary.extend(f"- {item.ref_id}" for item in self.inputs)
        prompt = (
            "以下是 ProjectOS 控制面生成的结构化任务输入包。它是当前 WorkItem 的唯一任务边界；"
            "输入引用只允许通过列出的 ref_id 按需读取，不能把引用之外的文件当作输入。\n\n"
            + "\n".join(summary)
            + "\n\n"
            + json.dumps({"task_input": self.as_dict()}, ensure_ascii=False, indent=2)
            + "\n\n请只完成 objective、constraints 和 acceptance_criteria 范围内的工作。"
        )
        if memory_context:
            prompt += "\n\n" + memory_context
        return prompt


def build_task_input(state: "RunState", item: WorkItem) -> TaskInputPackage:
    """从可信 RunState 和 WorkItem 生成当前节点的输入包。"""

    dependency_summaries: list[DependencySummary] = []
    for dependency in item.dependencies:
        predecessor = state.plan.work_item(dependency.work_item_id)
        if predecessor is None:
            continue
        result = state.node_results.get(predecessor.id)
        status = result.status.value if result is not None else "pending"
        dependency_summaries.append(
            DependencySummary(
                work_item_id=predecessor.id,
                agent_id=predecessor.agent_id,
                artifact_key=predecessor.artifact_key or predecessor.output_key,
                status=status,
                output_available=predecessor.output_key in state.artifacts,
            )
        )

    slot = item.output_slot
    allowed_paths, forbidden_paths = _scope_paths(item.execution_mode, slot)
    constraints = list(item.constraints)
    constraints.extend(_mode_constraints(item.execution_mode, slot))
    return TaskInputPackage(
        trace_id=state.plan.trace.trace_id,
        work_item_id=item.id,
        agent_id=item.agent_id,
        project_goal=state.plan.goal,
        objective=item.objective,
        execution_mode=item.execution_mode.value,
        scope=TaskScope(
            execution_mode=item.execution_mode.value,
            output_slot=slot,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
        ),
        inputs=tuple(InputBinding.from_ref(ref) for ref in item.input_refs),
        dependencies=tuple(dependency_summaries),
        output=OutputContract(
            output_key=item.output_key,
            artifact_key=item.artifact_key or item.output_key,
            output_slot=item.output_slot,
            publish_target=item.publish_target,
            expected_paths=allowed_paths,
        ),
        acceptance_criteria=item.acceptance_criteria,
        constraints=tuple(dict.fromkeys(constraints)),
        non_goals=item.non_goals,
        failure_context=(
            item.failure_package.as_task_text()
            if item.failure_package is not None
            else None
        ),
    )


def _scope_paths(
    execution_mode: ExecutionMode, output_slot: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if execution_mode is ExecutionMode.PARTITIONED and output_slot:
        if output_slot in {"backend", "frontend"}:
            allowed = (f"workspace/{output_slot}/**",)
            forbidden = (
                "workspace/frontend/**" if output_slot == "backend" else "workspace/backend/**",
                "workspace/tests/**",
                ".projectos/**",
            )
        else:
            allowed = (f"staged/{output_slot}/**",)
            forbidden = ("workspace/**", ".projectos/**")
        return allowed, forbidden
    if execution_mode is ExecutionMode.INTEGRATION:
        return ("workspace/**",), (".projectos/**", "project.yaml", "runtime.yaml")
    if execution_mode is ExecutionMode.QUALITY_GATE:
        return (), ("workspace/**", ".projectos/**")
    return (), ("任意未声明路径",)


def _mode_constraints(execution_mode: ExecutionMode, output_slot: str | None) -> tuple[str, ...]:
    if execution_mode is ExecutionMode.PARTITIONED:
        return (
            f"只能写入 {output_slot} 分区的 task worktree。",
            "不能直接修改正式 workspace、其他分区或 ProjectOS 控制面文件。",
        )
    if execution_mode is ExecutionMode.INTEGRATION:
        return ("只能整合 input_refs 中列出的分区输出。",)
    if execution_mode is ExecutionMode.QUALITY_GATE:
        return ("只能根据确定性 Policy 结果决定是否发布，不能自行修改候选内容。",)
    return ()


def _input_purpose(ref: ArtifactRef) -> str:
    if ref.layer == "staged":
        return f"读取 {ref.slot} 分区的授权暂存输出；只用于当前集成范围。"
    return {
        "requirement": "业务目标、范围和用户约束；只在技术设计无法回答时读取。",
        "architecture": "模块边界、接口契约、数据流和技术决策。",
        "tasks": "上游任务交接信息；当前 WorkItem 的 objective 和验收标准优先。",
        "environment": "运行时 profile、技术栈、依赖状态和测试命令。",
        "implementation": "已发布实现摘要；用于验证当前交付状态，不作为写入授权。",
        "tests": "已有测试报告和验证证据。",
    }.get(ref.artifact_key, "当前 WorkItem 明确授权的前置产物。")

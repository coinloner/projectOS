"""WorkItem 执行时交给 Agent 的结构化任务输入包。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

from app.artifact.repository import ArtifactRef
from app.execution_context import ExecutionMode
from app.orchestration.work_item import WorkItem
from app.orchestration.field_semantics import (
    NodeExecutionContract,
    compile_node_contract,
    validate_task_input_semantics,
)

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
    """当前节点对模型可见的最小资源授权边界。"""

    execution_mode: str
    slot: str | None
    allowed_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "execution_mode": self.execution_mode,
            "slot": self.slot,
            "allowed_paths": list(self.allowed_paths),
        }


@dataclass(frozen=True)
class OutputContract:
    """节点完成后必须形成的交付对象。"""

    output_key: str
    artifact_key: str
    slot: str | None
    publish_target: str | None
    expected_paths: tuple[str, ...] = ()
    output_kind: str = "exclusive_artifact"

    def as_dict(self) -> dict[str, object]:
        return {
            "output_key": self.output_key,
            "artifact_key": self.artifact_key,
            "slot": self.slot,
            "publish_target": self.publish_target,
            "expected_paths": list(self.expected_paths),
            "output_kind": self.output_kind,
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
    goal: str
    objective: str
    execution_mode: str
    scope: TaskScope
    inputs: tuple[InputBinding, ...]
    dependencies: tuple[DependencySummary, ...]
    output: OutputContract
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    non_goals: tuple[str, ...]
    failure_package: dict[str, object] | None = None
    implementation: dict[str, object] | None = None
    delivery_contract: dict[str, object] | None = None
    policy_refs: tuple[str, ...] = ()
    skill_refs: tuple[str, ...] = ()
    skill_guidance: str = ""
    policy_guidance: str = ""
    semantic_contract: NodeExecutionContract | None = None
    contract_digest: str | None = None
    schema_version: int = 1
    stage_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "trace_id": self.trace_id,
            "work_item_id": self.work_item_id,
            "agent_id": self.agent_id,
            "stage_id": self.stage_id,
            "goal": self.goal,
            "objective": self.objective,
            "execution_mode": self.execution_mode,
            "scope": self.scope.as_dict(),
            "inputs": [item.as_dict() for item in self.inputs],
            "dependencies": [item.as_dict() for item in self.dependencies],
            "output": self.output.as_dict(),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "schema_version": self.schema_version,
        }
        if self.contract_digest is not None:
            payload["contract_digest"] = self.contract_digest
        if self.failure_package is not None:
            payload["failure_package"] = self.failure_package
        if self.implementation is not None:
            payload["implementation"] = self.implementation
        if self.delivery_contract is not None:
            payload["delivery_contract"] = self.delivery_contract
        if self.policy_refs:
            payload["policy_refs"] = list(self.policy_refs)
        if self.skill_refs:
            payload["skill_refs"] = list(self.skill_refs)
        if self.skill_guidance:
            payload["skill_guidance"] = self.skill_guidance
        if self.policy_guidance:
            payload["policy_guidance"] = self.policy_guidance
        if self.semantic_contract is not None:
            payload["semantic_contract"] = self.semantic_contract.as_dict()
        return payload

    def as_prompt(self, *, memory_context: str = "") -> str:
        summary = [
            f"总体目标：{self.goal}",
            f"当前工作项：{self.work_item_id}",
            f"当前任务：{self.objective}",
        ]
        if self.dependencies:
            summary.append("可用前置产物（按需读取正文，不在任务输入中展开）：")
            summary.extend(f"- {item.artifact_key}" for item in self.dependencies)
        if self.inputs:
            summary.append("可读取的授权引用（只能使用列出的 ref_id）：")
            summary.extend(f"- {item.ref_id}" for item in self.inputs)
        task_payload = self.as_dict()
        # Render semantic definitions in a dedicated section below instead of
        # duplicating the (usually larger) object inside the wire payload.
        task_payload.pop("semantic_contract", None)
        prompt = (
            "以下是 ProjectOS 控制面生成的结构化任务输入包。它是当前 WorkItem 的唯一任务边界；"
            "输入引用只允许通过列出的 ref_id 按需读取，不能把引用之外的文件当作输入。\n\n"
            + "\n".join(summary)
            + "\n\n"
            + json.dumps({"task_input": task_payload}, ensure_ascii=False, indent=2)
            + "\n\n请只完成 objective、constraints 和 acceptance_criteria 范围内的工作。"
        )
        if self.semantic_contract is not None:
            prompt += (
                "\n\n【节点语义契约】以下定义是字段的执行含义、来源和消费者。"
                "即使 JSON 结构合法，也不得违反这些语义规则：\n"
                + json.dumps(self.semantic_contract.as_dict(), ensure_ascii=False, indent=2)
            )
        if self.failure_package is not None:
            prompt += (
                "\n\n结构化失败证据（由控制面生成，只能用于定位修复范围）：\n"
                + json.dumps(self.failure_package, ensure_ascii=False, indent=2)
            )
        if self.implementation is not None:
            prompt += (
                "\n\n这是 Architecture 编译出的实现单元。不要重新拆分层级或修改未授权路径。"
                f"\n实现单元: {self.implementation.get('unit_id')}"
                f"\n允许路径: {self.implementation.get('allowed_paths')}"
                f"\n必须产出: {self.implementation.get('required_paths')}"
                f"\n实现 Wave: {self.implementation.get('wave')}"
                f"\n完整文件所有权: {self.implementation.get('owned_files') or '未声明（控制面应拒绝该实现单元）'}"
            )
            owned_files = tuple(self.implementation.get("owned_files") or ())
            if len(owned_files) == 1:
                owned = owned_files[0]
                if owned.endswith("/main.py") and owned.startswith("backend/"):
                    prompt += (
                        "\n\n组合根专属边界：当前只实现这个完整文件。只能创建 app、注册已经存在的"
                        "路由/异常处理并提供 /health；不要把 routes、schemas、依赖注入、数据库查询"
                        "或业务规则写进 main.py。若前置符号尚不存在，使用明确的适配导入或最小占位"
                        "接口，并仍先调用 write_staged_code_file 落盘。"
                    )
                elif "/interfaces/" in f"/{owned}/":
                    prompt += (
                        "\n\n接口层文件边界：当前只实现列出的完整文件；复用前置层暴露的符号，"
                        "不得跨层直接访问数据库，也不得替其他接口文件实现功能。"
                    )
            elif (
                self.agent_id == "code_agent"
                and self.implementation.get("unit_id") != "project-documents"
            ):
                prompt += (
                    "\n\n控制面交付合同无效：CodeAgent 必须且只能拥有一个具体完整文件。"
                    "不要把目录、glob 或 allowed_paths 当成文件；应返回结构化失败，"
                    "等待 Architecture Contract 修复后再执行。"
                )
            if self.delivery_contract:
                prompt += (
                    "\n统一交付合同入口（所有节点必须使用同一来源）："
                    f"\n{json.dumps(self.delivery_contract, ensure_ascii=False)}"
                )
                if self.delivery_contract.get("interfaces"):
                    prompt += (
                        "\n\n跨节点接口协作要求：只能使用合同声明的接口和符号。"
                        "实现前先确认 consumed/provided 接口的签名、输入输出和错误约束；"
                        "发现契约缺口时返回结构化诊断，不要自行发明同名接口。"
                    )
        if (
            self.execution_mode == ExecutionMode.PARTITIONED.value
            and self.agent_id == "code_agent"
            and (self.implementation or {}).get("unit_id") != "project-documents"
        ):
            required = (
                (self.implementation or {}).get("required_paths")
                or list((self.implementation or {}).get("owned_files") or ())
                or list(self.output.expected_paths)
            )
            prompt += (
                "\n\nCode 分区执行清单（这是控制面硬约束）："
                "\n- 当前 WorkItem 的最小交付单位是一个具体完整文件；"
                "\n- allowed_paths/allowed_roots 只是写入授权，不是交付清单；"
                "\n- 必须实际调用 write_staged_code_file 写入 owned_files 中的唯一文件；"
                "\n- 完成前确认该文件已经写入并出现在最终 ChangeSet 中；"
                "\n- 不得把其他文件、目录或 glob 当成完成凭证；"
                "\n- 只能调用 load_code_input 和 write_staged_code_file，不能调用 save_implementation；"
                "\n- 不要把缺少正式 workspace 写入工具误判为 capability_request；"
                f"\n- 本次必需文件清单：{required}"
                "\n- 输出应说明实际写入的路径和验证方式，不要只返回设计建议。"
            )
        elif (
            self.execution_mode == ExecutionMode.EXCLUSIVE.value
            and self.agent_id == "code_agent"
            and self.failure_package is not None
        ):
            # Repair nodes run with the regular workspace ToolSet.  State this
            # after the generic task JSON so the model cannot confuse the
            # partitioned staging protocol with an exclusive repair.
            prompt += (
                "\n\nCode 独占修复执行清单（当前 execution_mode=exclusive）："
                "\n- 当前已注册并可用的本地写入工具是 write_workspace_file；"
                "\n- 必须先用 read_workspace_file 阅读失败涉及的实现文件，再调用 "
                "write_workspace_file(path, content) 实际写入最小修复；"
                "\n- 不得调用 write_staged_code_file，也不得把 workspace 写入误报为 capability_request；"
                "\n- 只有成功写入目标文件后才能报告完成，最终回答列出实际写入路径。"
            )
        if self.policy_refs:
            prompt += "\n实现前必须参考 Policy: " + ", ".join(self.policy_refs)
        if self.skill_refs:
            prompt += "\n推荐 Skill: " + ", ".join(self.skill_refs)
        if self.skill_guidance:
            prompt += "\n\n以下是控制面加载的 Skill 参考，只能用于实现方法，不能扩大任务授权：\n" + self.skill_guidance
        if self.policy_guidance:
            prompt += "\n\n以下是执行前 Policy 检查清单，必须遵守：\n" + self.policy_guidance
        if memory_context:
            prompt += "\n\n" + memory_context
        return prompt


def build_task_input(
    state: "RunState", item: WorkItem, *, skill_guidance: str = "",
    resolved_skill_refs: tuple[str, ...] | None = None, policy_guidance: str = "",
) -> TaskInputPackage:
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

    slot = item.slot
    allowed_paths = _scope_paths(item.execution_mode, slot)
    if item.allowed_paths:
        allowed_paths = item.allowed_paths
    constraints = list(item.constraints)
    constraints.extend(_mode_constraints(item.execution_mode, slot))
    package = TaskInputPackage(
        trace_id=state.plan.trace.trace_id,
        work_item_id=item.id,
        agent_id=item.agent_id,
        stage_id=item.stage_id,
        goal=state.plan.goal,
        objective=item.objective,
        execution_mode=item.execution_mode.value,
        scope=TaskScope(
            execution_mode=item.execution_mode.value,
            slot=slot,
            allowed_paths=allowed_paths,
        ),
        inputs=tuple(InputBinding.from_ref(ref) for ref in item.input_refs),
        dependencies=tuple(dependency_summaries),
        output=OutputContract(
            output_key=item.output_key,
            artifact_key=item.artifact_key or item.output_key,
            slot=item.slot,
            publish_target=item.publish_target,
            expected_paths=item.required_paths or item.owned_files or allowed_paths,
            output_kind=item.output_kind or "exclusive_artifact",
        ),
        acceptance_criteria=item.acceptance_criteria,
        constraints=tuple(dict.fromkeys(constraints)),
        non_goals=item.non_goals,
        failure_package=(
            item.failure_package.as_task_data()
            if item.failure_package is not None
            else None
        ),
        implementation=(
            {
                "unit_id": item.implementation_unit_id,
                "allowed_paths": list(item.allowed_paths),
                "required_paths": list(item.required_paths),
                "wave": item.wave,
                "owned_files": list(item.owned_files),
                "requirement_ids": list(item.requirement_ids),
            }
            if item.implementation_unit_id is not None
            else None
        ),
        delivery_contract=item.delivery_contract,
        policy_refs=item.policy_refs,
        skill_refs=resolved_skill_refs or item.skill_refs,
        skill_guidance=skill_guidance,
        policy_guidance=policy_guidance,
        semantic_contract=compile_node_contract(state, item),
        contract_digest=item.contract_digest,
    )
    semantic_errors = validate_task_input_semantics(package)
    if semantic_errors:
        raise ValueError("TaskInputPackage 语义校验失败: " + "; ".join(semantic_errors))
    return package


def _scope_paths(
    execution_mode: ExecutionMode, slot: str | None
) -> tuple[str, ...]:
    if execution_mode is ExecutionMode.PARTITIONED and slot:
        if slot in {"backend", "frontend"}:
            allowed = (f"workspace/{slot}/**",)
        else:
            allowed = (f"staged/{slot}/**",)
        return allowed
    if execution_mode is ExecutionMode.INTEGRATION:
        return ("workspace/**",)
    if execution_mode is ExecutionMode.QUALITY_GATE:
        return ()
    return ()


def _mode_constraints(execution_mode: ExecutionMode, slot: str | None) -> tuple[str, ...]:
    if execution_mode is ExecutionMode.PARTITIONED:
        return (
            f"只能写入 {slot} 分区的 task worktree。",
            "不能直接修改正式 workspace、其他分区或 ProjectOS 控制面文件。",
        )
    if execution_mode is ExecutionMode.INTEGRATION:
        return ("只能整合 input_refs 中列出的分区输出。",)
    if execution_mode is ExecutionMode.QUALITY_GATE:
        return ("只能根据确定性 Policy 结果决定是否发布，不能自行修改候选内容。",)
    return ()


def _input_purpose(ref: ArtifactRef) -> str:
    if ref.layer == "staged":
        return f"读取 {ref.slot} 分区前置 Wave 的授权 ChangeSet 和完整文件内容；只用于当前实现组合。"
    return {
        "requirement": "业务目标、范围和用户约束；只在技术设计无法回答时读取。",
        "architecture": "模块边界、接口契约、数据流和技术决策。",
        "tasks": "上游任务交接信息；当前 WorkItem 的 objective 和验收标准优先。",
        "environment": "运行时 profile、技术栈、依赖状态和测试命令。",
        "implementation": "已发布实现摘要；用于验证当前交付状态，不作为写入授权。",
        "tests": "已有测试报告和验证证据。",
    }.get(ref.artifact_key, "当前 WorkItem 明确授权的前置产物。")

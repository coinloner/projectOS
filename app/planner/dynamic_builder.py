"""将 ArchitectureBlueprint 编译为项目专属的模块设计执行节点。

流程阶段由 ``ProcessDefinition`` 固定，模块数量、业务目的和依赖关系由
Blueprint 决定。本模块只负责控制面编译，不调用 LLM，也不让模型提供执行权限。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from app.artifact.repository import ArtifactRef
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    ModuleDesign,
)
from app.orchestration.plan import ExecutionPlan
from app.orchestration.work_item import (
    DependencySource,
    WorkItem,
    WorkItemDependency,
)
from app.process import ProcessDefinition, default_process_registry
from app.execution_context import ExecutionMode


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


class BlueprintValidationError(ValueError):
    """Blueprint 不能被安全地编译为动态模块计划。"""


@dataclass(frozen=True)
class BlueprintExpansion:
    """一次 Blueprint 扩展的结果和可审计来源。"""

    plan: ExecutionPlan
    blueprint_design_id: str
    blueprint_work_item_id: str
    added_work_item_ids: tuple[str, ...]
    integration_work_item_id: str | None
    module_waves: dict[str, int]
    blueprint_digest: str
    parent_plan_revision: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "architecture_blueprint_expansion",
            "plan_id": self.plan.id,
            "trace_id": self.plan.trace.trace_id,
            "blueprint_design_id": self.blueprint_design_id,
            "blueprint_work_item_id": self.blueprint_work_item_id,
            "blueprint_digest": self.blueprint_digest,
            "parent_plan_revision": self.parent_plan_revision,
            "added_work_item_ids": list(self.added_work_item_ids),
            "integration_work_item_id": self.integration_work_item_id,
            "module_waves": dict(self.module_waves),
        }


@dataclass(frozen=True)
class ImplementationExpansion:
    """一次 ModuleDesign -> ImplementationDesign 的动态扩展结果。"""

    plan: ExecutionPlan
    blueprint_design_id: str
    blueprint_work_item_id: str
    module_design_ids: tuple[str, ...]
    module_work_item_ids: dict[str, str]
    added_work_item_ids: tuple[str, ...]
    integration_work_item_id: str | None
    implementation_waves: dict[str, int]
    blueprint_digest: str
    parent_plan_revision: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "architecture_implementation_expansion",
            "plan_id": self.plan.id,
            "trace_id": self.plan.trace.trace_id,
            "blueprint_design_id": self.blueprint_design_id,
            "blueprint_work_item_id": self.blueprint_work_item_id,
            "module_design_ids": list(self.module_design_ids),
            "module_work_item_ids": dict(self.module_work_item_ids),
            "blueprint_digest": self.blueprint_digest,
            "parent_plan_revision": self.parent_plan_revision,
            "added_work_item_ids": list(self.added_work_item_ids),
            "integration_work_item_id": self.integration_work_item_id,
            "implementation_waves": dict(self.implementation_waves),
        }


class BlueprintValidator:
    """验证 Blueprint 中由业务侧决定的模块语义和依赖关系。"""

    def __init__(self, process: ProcessDefinition | None = None) -> None:
        self._process = process or default_process_registry().get("software_delivery")
        if self._process is None:  # pragma: no cover - defensive registry guard
            raise RuntimeError("software_delivery 流程未注册")

    def validate(self, blueprint: ArchitectureBlueprint) -> None:
        limits = self._process.limits
        if blueprint.depth != 0:
            raise BlueprintValidationError("动态模块扩展要求 depth=0 的 ArchitectureBlueprint")
        if len(blueprint.modules) > limits.max_modules:
            raise BlueprintValidationError(
                f"模块数量 {len(blueprint.modules)} 超过流程上限 {limits.max_modules}"
            )
        module_ids = [module.module_id for module in blueprint.modules]
        if len(module_ids) != len(set(module_ids)):
            raise BlueprintValidationError("Blueprint 模块 module_id 必须唯一")
        known = set(module_ids)
        edges: dict[str, set[str]] = {}
        for module in blueprint.modules:
            if not (module.purpose or "").strip():
                raise BlueprintValidationError(f"模块 {module.module_id} 缺少业务 purpose")
            dependencies = set(module.depends_on_modules)
            unknown = sorted(dependencies - known)
            if unknown:
                raise BlueprintValidationError(
                    f"模块 {module.module_id} 依赖不存在的模块: {', '.join(unknown)}"
                )
            if module.module_id in dependencies:
                raise BlueprintValidationError(f"模块 {module.module_id} 不能依赖自身")
            edges[module.module_id] = dependencies
        pending = {key: set(value) for key, value in edges.items()}
        resolved: set[str] = set()
        while pending:
            ready = {key for key, value in pending.items() if value <= resolved}
            if not ready:
                raise BlueprintValidationError("Blueprint 模块依赖存在循环")
            resolved.update(ready)
            for key in ready:
                pending.pop(key)


class DynamicPlanBuilder:
    """根据 Blueprint 真实模块清单生成 depth=1 执行节点。"""

    def __init__(self, process: ProcessDefinition | None = None) -> None:
        self._process = process or default_process_registry().get("software_delivery")
        if self._process is None:  # pragma: no cover - defensive registry guard
            raise RuntimeError("software_delivery 流程未注册")
        self._validator = BlueprintValidator(self._process)

    def expand_modules(
        self,
        plan: ExecutionPlan,
        blueprint: ArchitectureBlueprint,
        *,
        blueprint_work_item_id: str,
        integration_work_item_id: str | None = None,
        parent_plan_revision: int | None = None,
    ) -> BlueprintExpansion:
        self._validator.validate(blueprint)
        blueprint_item = plan.work_item(blueprint_work_item_id)
        if blueprint_item is None:
            raise BlueprintValidationError(
                f"Blueprint WorkItem 不存在: {blueprint_work_item_id}"
            )
        if blueprint_item.agent_id != "architecture_agent":
            raise BlueprintValidationError("Blueprint WorkItem 必须由 architecture_agent 执行")
        if blueprint_item.execution_mode is not ExecutionMode.PARTITIONED:
            raise BlueprintValidationError("Blueprint WorkItem 必须是 PARTITIONED")

        existing_ids = {item.id for item in plan.work_items}
        has_existing_module_items = any(
            item.stage_id == "architecture_module"
            or (item.slot or "").startswith("module-")
            for item in plan.work_items
        )
        if has_existing_module_items:
            raise BlueprintValidationError("当前计划已经完成 Blueprint 模块扩展")

        safe_ids: dict[str, str] = {}
        for module in blueprint.modules:
            safe = _safe_component(module.module_id)
            if not safe:
                raise BlueprintValidationError(f"模块 module_id 无法生成安全节点 ID: {module.module_id}")
            if safe in safe_ids.values():
                raise BlueprintValidationError("不同 module_id 归一化后产生节点 ID 冲突")
            safe_ids[module.module_id] = safe

        module_map = {module.module_id: module for module in blueprint.modules}
        waves: dict[str, int] = {}

        def wave(module_id: str, visiting: set[str] | None = None) -> int:
            if module_id in waves:
                return waves[module_id]
            visiting = visiting or set()
            if module_id in visiting:
                raise BlueprintValidationError("Blueprint 模块依赖存在循环")
            visiting.add(module_id)
            value = max(
                (wave(dep, visiting) + 1 for dep in module_map[module_id].depends_on_modules),
                default=0,
            )
            visiting.remove(module_id)
            waves[module_id] = value
            return value

        for module_id in module_map:
            wave(module_id)

        blueprint_ref = ArtifactRef.staged(
            artifact_key=blueprint_item.artifact_key or "architecture",
            trace_id=plan.trace.trace_id,
            work_item_id=blueprint_item.id,
            slot=blueprint_item.slot or "blueprint",
        )
        ids = {
            module_id: f"wi-architecture-module-{safe_ids[module_id]}"
            for module_id in module_map
        }
        if existing_ids.intersection(ids.values()):
            raise BlueprintValidationError("动态模块 WorkItem ID 与现有计划冲突")

        added: list[WorkItem] = []
        for module in blueprint.modules:
            dependencies = [
                WorkItemDependency(
                    blueprint_item.id,
                    DependencySource.SYSTEM,
                    "architecture_blueprint:module",
                )
            ]
            refs = [blueprint_ref]
            for dependency in module.depends_on_modules:
                dependencies.append(
                    WorkItemDependency(
                        ids[dependency],
                        DependencySource.SYSTEM,
                        "architecture_module:depends_on_modules",
                    )
                )
                refs.append(
                    ArtifactRef.staged(
                        artifact_key="architecture",
                        trace_id=plan.trace.trace_id,
                        work_item_id=ids[dependency],
                        slot=f"module-{safe_ids[dependency]}",
                    )
                )
            purpose = module.purpose or module.responsibility
            dependency_text = ", ".join(module.depends_on_modules) or "无"
            added.append(
                WorkItem(
                    id=ids[module.module_id],
                    agent_id="architecture_agent",
                    stage_id="architecture_module",
                    objective=(
                        f"围绕模块 {module.module_id} 的业务目的：{purpose}；"
                        f"在不改变该目的的前提下，细化模块职责、实体、接口和验收边界。"
                    ),
                    output_key=f"architecture_module_{safe_ids[module.module_id]}",
                    artifact_key="architecture",
                    dependencies=tuple(dependencies),
                    acceptance_criteria=(
                        "只能调用 write_module_design 保存一个 depth=1 ModuleDesign。",
                        f"module_id 必须为 {module.module_id}，parent_design_id 必须为 {blueprint.design_id}。",
                        f"purpose 必须保留 Blueprint 业务目的；depends_on_modules 必须为 [{dependency_text}]。",
                    ),
                    constraints=(
                        "只能细化当前模块，不得新增 Blueprint 未声明的模块、业务能力或跨层依赖。",
                        "不得修改总体蓝图、接口 owner 或其他模块的文件所有权。",
                    ),
                    execution_mode=ExecutionMode.PARTITIONED,
                    input_refs=tuple(refs),
                    slot=f"module-{safe_ids[module.module_id]}",
                    output_kind="ModuleDesign",
                    requirement_ids=tuple(module.requirement_ids),
                    wave=waves[module.module_id],
                    delivery_contract={
                        "architecture": {
                            "depth": 1,
                            "design_id": f"module-{safe_ids[module.module_id]}",
                            "parent_design_id": blueprint.design_id,
                            "module_id": module.module_id,
                            "purpose": purpose,
                            "depends_on_modules": list(module.depends_on_modules),
                        }
                    },
                )
            )

        integration_id = integration_work_item_id
        if integration_id is not None:
            integration = plan.work_item(integration_id)
            if integration is None:
                raise BlueprintValidationError(f"架构集成 WorkItem 不存在: {integration_id}")
            if integration.execution_mode is not ExecutionMode.INTEGRATION:
                raise BlueprintValidationError("架构集成 WorkItem 必须是 INTEGRATION")
            module_dependencies = tuple(
                WorkItemDependency(
                    ids[module.module_id],
                    DependencySource.SYSTEM,
                    "architecture_blueprint:all_modules",
                )
                for module in blueprint.modules
            )
            merged_dependencies: list[WorkItemDependency] = []
            seen: set[str] = set()
            for dependency in (*integration.dependencies, *module_dependencies):
                if dependency.work_item_id not in seen:
                    seen.add(dependency.work_item_id)
                    merged_dependencies.append(dependency)
            merged_refs = list(integration.input_refs)
            known_refs = {ref.ref_id for ref in merged_refs}
            if blueprint_ref.ref_id not in known_refs:
                merged_refs.append(blueprint_ref)
                known_refs.add(blueprint_ref.ref_id)
            for module in blueprint.modules:
                ref = ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=plan.trace.trace_id,
                    work_item_id=ids[module.module_id],
                    slot=f"module-{safe_ids[module.module_id]}",
                )
                if ref.ref_id not in known_refs:
                    merged_refs.append(ref)
                    known_refs.add(ref.ref_id)
            updated_integration = replace(
                integration,
                dependencies=tuple(merged_dependencies),
                input_refs=tuple(merged_refs),
                contract_digest=None,
            )
            work_items = tuple(
                updated_integration if item.id == integration_id else item
                for item in plan.work_items
            ) + tuple(added)
        else:
            work_items = tuple(plan.work_items) + tuple(added)

        expanded_plan = ExecutionPlan(
            id=plan.id,
            goal=plan.goal,
            work_items=work_items,
            template_id=plan.template_id,
            process_id=plan.process_id,
            trace=plan.trace,
        )
        return BlueprintExpansion(
            plan=expanded_plan,
            blueprint_design_id=blueprint.design_id,
            blueprint_work_item_id=blueprint_work_item_id,
            added_work_item_ids=tuple(item.id for item in added),
            integration_work_item_id=integration_id,
            module_waves=waves,
            blueprint_digest=hashlib.sha256(
                json.dumps(
                    blueprint.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            parent_plan_revision=parent_plan_revision,
        )

    def expand_implementations(
        self,
        plan: ExecutionPlan,
        blueprint: ArchitectureBlueprint,
        module_designs: Sequence[ModuleDesign],
        *,
        blueprint_work_item_id: str,
        module_work_item_ids: Mapping[str, str] | None = None,
        integration_work_item_id: str | None = None,
        parent_plan_revision: int | None = None,
    ) -> ImplementationExpansion:
        """根据已经完成的 ModuleDesign 动态生成 depth=2 实现设计节点。

        该扩展只建立架构设计层的 WorkItem，不读取或修改代码合同。每个模块
        恰好对应一个实现设计节点，节点只依赖自己的 ModuleDesign；模块之间
        的业务依赖通过输入引用传递，避免把尚未完成的实现设计误当作前置条件。
        """
        self._validator.validate(blueprint)
        blueprint_item = plan.work_item(blueprint_work_item_id)
        if blueprint_item is None:
            raise BlueprintValidationError(
                f"Blueprint WorkItem 不存在: {blueprint_work_item_id}"
            )
        if blueprint_item.agent_id != "architecture_agent":
            raise BlueprintValidationError("Blueprint WorkItem 必须由 architecture_agent 执行")

        integration = None
        if integration_work_item_id is not None:
            integration = plan.work_item(integration_work_item_id)
            if integration is None:
                raise BlueprintValidationError(
                    f"架构集成 WorkItem 不存在: {integration_work_item_id}"
                )
            if integration.execution_mode is not ExecutionMode.INTEGRATION:
                raise BlueprintValidationError("架构集成 WorkItem 必须是 INTEGRATION")

        if any(
            item.stage_id == "architecture_implementation"
            or (item.slot or "").startswith("implementation-")
            for item in plan.work_items
        ):
            raise BlueprintValidationError("当前计划已经完成 Architecture Implementation 扩展")

        blueprint_modules = {module.module_id: module for module in blueprint.modules}
        designs_by_module: dict[str, ModuleDesign] = {}
        for design in module_designs:
            if design.depth != 1:
                raise BlueprintValidationError(
                    f"模块设计 {design.design_id} 必须是 depth=1"
                )
            if design.parent_design_id != blueprint.design_id:
                raise BlueprintValidationError(
                    f"模块设计 {design.design_id} 未引用当前总体蓝图"
                )
            if design.module_id not in blueprint_modules:
                raise BlueprintValidationError(
                    f"模块设计 {design.module_id} 不在总体蓝图模块清单中"
                )
            if design.module_id in designs_by_module:
                raise BlueprintValidationError(
                    f"模块 {design.module_id} 存在多个 ModuleDesign"
                )
            blueprint_module = blueprint_modules[design.module_id]
            if not design.purpose or design.purpose != blueprint_module.purpose:
                raise BlueprintValidationError(
                    f"模块设计 {design.module_id} 的 purpose 必须与 Blueprint 一致"
                )
            # ModuleDesigns produced before the dependency field was added may
            # deserialize an omitted value as ``[]``.  In that migration case
            # inherit the authoritative Blueprint edge; any non-empty mismatch
            # remains a hard semantic error.
            effective_dependencies = (
                list(blueprint_module.depends_on_modules)
                if not design.depends_on_modules and blueprint_module.depends_on_modules
                else list(design.depends_on_modules)
            )
            if set(effective_dependencies) != set(blueprint_module.depends_on_modules):
                raise BlueprintValidationError(
                    f"模块设计 {design.module_id} 的 depends_on_modules 必须与 Blueprint 一致"
                )
            if effective_dependencies != list(design.depends_on_modules):
                design = design.model_copy(update={"depends_on_modules": effective_dependencies})
            designs_by_module[design.module_id] = design

        missing = sorted(set(blueprint_modules) - set(designs_by_module))
        extra = sorted(set(designs_by_module) - set(blueprint_modules))
        if missing:
            raise BlueprintValidationError("缺少 ModuleDesign: " + ", ".join(missing))
        if extra:  # defensive; the loop above already rejects extras
            raise BlueprintValidationError("存在未声明模块的 ModuleDesign: " + ", ".join(extra))

        supplied_ids = dict(module_work_item_ids or {})
        resolved_module_items: dict[str, str] = {}
        for module_id in blueprint_modules:
            candidate = supplied_ids.get(module_id)
            if candidate is None:
                matches: list[WorkItem] = []
                for item in plan.work_items:
                    if item.stage_id != "architecture_module" or item.agent_id != "architecture_agent":
                        continue
                    declared = None
                    if item.delivery_contract:
                        architecture = item.delivery_contract.get("architecture")
                        if isinstance(architecture, dict):
                            declared = architecture.get("module_id")
                    if declared == module_id or (item.slot or "") == f"module-{_safe_component(module_id)}":
                        matches.append(item)
                if len(matches) == 1:
                    candidate = matches[0].id
            module_item = plan.work_item(candidate) if candidate else None
            if module_item is None:
                raise BlueprintValidationError(
                    f"模块 {module_id} 缺少对应的 architecture_module WorkItem"
                )
            if module_item.agent_id != "architecture_agent" or module_item.execution_mode is not ExecutionMode.PARTITIONED:
                raise BlueprintValidationError(
                    f"模块 {module_id} 的 WorkItem 不是合法 PARTITIONED 架构节点"
                )
            resolved_module_items[module_id] = module_item.id

        # ModuleDesign deliberately does not declare implementation units yet;
        # the per-project unit limit is enforced when the resulting
        # ArchitectureDesignBundle is integrated.  At this boundary the
        # conservative bound is one implementation design per Blueprint
        # module, already constrained by ``max_modules``.

        safe_ids: dict[str, str] = {}
        for module_id in blueprint_modules:
            safe = _safe_component(module_id)
            if not safe:
                raise BlueprintValidationError(
                    f"模块 module_id 无法生成安全节点 ID: {module_id}"
                )
            if safe in safe_ids.values():
                raise BlueprintValidationError("不同 module_id 归一化后产生节点 ID 冲突")
            safe_ids[module_id] = safe

        module_waves = _module_waves(blueprint)
        existing_ids = {item.id for item in plan.work_items}
        ids = {
            module_id: f"wi-architecture-implementation-{safe_ids[module_id]}"
            for module_id in blueprint_modules
        }
        if existing_ids.intersection(ids.values()):
            raise BlueprintValidationError("动态实现设计 WorkItem ID 与现有计划冲突")

        blueprint_ref = ArtifactRef.staged(
            artifact_key=blueprint_item.artifact_key or "architecture",
            trace_id=plan.trace.trace_id,
            work_item_id=blueprint_item.id,
            slot=blueprint_item.slot or "blueprint",
        )
        added: list[WorkItem] = []
        for module_id, module in blueprint_modules.items():
            module_item_id = resolved_module_items[module_id]
            design = designs_by_module[module_id]
            module_ref = ArtifactRef.staged(
                artifact_key="architecture",
                trace_id=plan.trace.trace_id,
                work_item_id=module_item_id,
                slot=f"module-{safe_ids[module_id]}",
            )
            refs = [blueprint_ref, module_ref]
            for dependency in module.depends_on_modules:
                refs.append(
                    ArtifactRef.staged(
                        artifact_key="architecture",
                        trace_id=plan.trace.trace_id,
                        work_item_id=resolved_module_items[dependency],
                        slot=f"module-{safe_ids[dependency]}",
                    )
                )
            purpose = module.purpose or module.responsibility
            dependency_text = ", ".join(module.depends_on_modules) or "无"
            added.append(
                WorkItem(
                    id=ids[module_id],
                    agent_id="architecture_agent",
                    stage_id="architecture_implementation",
                    objective=(
                        f"将模块 {module_id}（业务目的：{purpose}）细化为可执行的文件级实现设计；"
                        "只定义实现单元、接口 ownership、依赖和测试边界，不编写代码。"
                    ),
                    output_key=f"architecture_implementation_{safe_ids[module_id]}",
                    artifact_key="architecture",
                    dependencies=(
                        WorkItemDependency(
                            module_item_id,
                            DependencySource.SYSTEM,
                            "architecture_module:implementation",
                        ),
                    ),
                    acceptance_criteria=(
                        "只能调用 write_implementation_design 保存一个 depth=2 ImplementationDesign。",
                        f"module_id 必须为 {module_id}，parent_design_id 必须为 {design.design_id}。",
                        "每个 implementation_unit 必须只拥有一个具体 owned_file；接口必须区分 provided_interfaces 和 consumed_interfaces。",
                        f"depends_on_modules 必须保持 Blueprint 声明：[{dependency_text}]。",
                    ),
                    constraints=(
                        "只能细化当前模块，不得新增 Blueprint 未声明的模块或业务能力。",
                        "不得修改 ModuleDesign、Blueprint 或其他模块的文件 ownership。",
                        "不得提前消费其他模块的 ImplementationDesign；跨模块关系只能通过已声明接口引用表达。",
                    ),
                    execution_mode=ExecutionMode.PARTITIONED,
                    input_refs=tuple(refs),
                    slot=f"implementation-{safe_ids[module_id]}",
                    output_kind="ImplementationDesign",
                    requirement_ids=tuple(module.requirement_ids),
                    wave=module_waves[module_id],
                    delivery_contract={
                        "architecture": {
                            "depth": 2,
                            "design_id": f"implementation-{safe_ids[module_id]}",
                            "parent_design_id": design.design_id,
                            "module_id": module_id,
                            "purpose": purpose,
                            "depends_on_modules": list(module.depends_on_modules),
                        }
                    },
                )
            )

        work_items: tuple[WorkItem, ...]
        if integration is not None:
            merged_dependencies: list[WorkItemDependency] = []
            seen_dependencies: set[str] = set()
            for dependency in (
                *integration.dependencies,
                *tuple(
                    WorkItemDependency(
                        ids[module_id],
                        DependencySource.SYSTEM,
                        "architecture_implementation:all_modules",
                    )
                    for module_id in blueprint_modules
                ),
            ):
                if dependency.work_item_id not in seen_dependencies:
                    seen_dependencies.add(dependency.work_item_id)
                    merged_dependencies.append(dependency)
            merged_refs = list(integration.input_refs)
            known_refs = {ref.ref_id for ref in merged_refs}
            for item in added:
                ref = ArtifactRef.staged(
                    artifact_key="architecture",
                    trace_id=plan.trace.trace_id,
                    work_item_id=item.id,
                    slot=item.slot or "",
                )
                if ref.ref_id not in known_refs:
                    merged_refs.append(ref)
                    known_refs.add(ref.ref_id)
            updated_integration = replace(
                integration,
                dependencies=tuple(merged_dependencies),
                input_refs=tuple(merged_refs),
                contract_digest=None,
            )
            # A dynamic Planner contract node usually starts with no
            # ``input_from`` declaration because the concrete module count is
            # unknown at planning time.  Wire it to the exact same frozen
            # design references as Architecture Integration now that the
            # Blueprint has materialized them.
            updated_contract_ids = {
                item.id
                for item in plan.work_items
                if item.stage_id == "contract"
                and integration.id in item.dependency_ids
            }
            work_items = tuple(
                updated_integration
                if item.id == integration.id
                else replace(item, input_refs=tuple(merged_refs), contract_digest=None)
                if item.id in updated_contract_ids
                else item
                for item in plan.work_items
            ) + tuple(added)
        else:
            work_items = tuple(plan.work_items) + tuple(added)

        expanded_plan = ExecutionPlan(
            id=plan.id,
            goal=plan.goal,
            work_items=work_items,
            template_id=plan.template_id,
            process_id=plan.process_id,
            trace=plan.trace,
        )
        return ImplementationExpansion(
            plan=expanded_plan,
            blueprint_design_id=blueprint.design_id,
            blueprint_work_item_id=blueprint_work_item_id,
            module_design_ids=tuple(
                designs_by_module[module_id].design_id
                for module_id in blueprint_modules
            ),
            module_work_item_ids=resolved_module_items,
            added_work_item_ids=tuple(item.id for item in added),
            integration_work_item_id=integration_work_item_id,
            implementation_waves=module_waves,
            blueprint_digest=_blueprint_digest(blueprint),
            parent_plan_revision=parent_plan_revision,
        )


def _safe_component(value: str) -> str:
    return _SAFE_COMPONENT.sub("-", value.strip()).strip("-_.")[:80]


__all__ = [
    "BlueprintExpansion",
    "BlueprintValidationError",
    "BlueprintValidator",
    "DynamicPlanBuilder",
    "ImplementationExpansion",
]


def _module_waves(blueprint: ArchitectureBlueprint) -> dict[str, int]:
    modules = {module.module_id: module for module in blueprint.modules}
    waves: dict[str, int] = {}

    def visit(module_id: str, visiting: set[str] | None = None) -> int:
        if module_id in waves:
            return waves[module_id]
        visiting = visiting or set()
        if module_id in visiting:
            raise BlueprintValidationError("Blueprint 模块依赖存在循环")
        visiting.add(module_id)
        value = max(
            (visit(dep, visiting) + 1 for dep in modules[module_id].depends_on_modules),
            default=0,
        )
        visiting.remove(module_id)
        waves[module_id] = value
        return value

    for module_id in modules:
        visit(module_id)
    return waves


def _blueprint_digest(blueprint: ArchitectureBlueprint) -> str:
    return hashlib.sha256(
        json.dumps(
            blueprint.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

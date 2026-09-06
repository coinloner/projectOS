"""Canonical names for values crossing orchestration boundaries.

Aliases are accepted only at an input boundary.  Persisted plans, execution
contexts and task input packages must use the canonical name returned here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SemanticFieldSpec:
    """A field's executable meaning at an orchestration boundary."""

    meaning: str
    source: str
    consumer: str
    required: bool = False
    value_rules: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "meaning": self.meaning,
            "source": self.source,
            "consumer": self.consumer,
            "required": self.required,
            "value_rules": list(self.value_rules),
        }


@dataclass(frozen=True)
class NodeExecutionContract:
    """Read-only semantic contract compiled for one WorkItem.

    The contract is intentionally a local projection.  Agents receive the
    definitions needed for their node and its immediate neighbours, not the
    entire execution graph.
    """

    node_type: str
    node_purpose: str
    fields: Mapping[str, SemanticFieldSpec]
    invariants: tuple[str, ...] = ()
    predecessors: tuple[dict[str, object], ...] = ()
    successors: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "node_type": self.node_type,
            "node_purpose": self.node_purpose,
            "fields": {name: spec.as_dict() for name, spec in self.fields.items()},
            "invariants": list(self.invariants),
            "predecessors": [dict(item) for item in self.predecessors],
            "successors": [dict(item) for item in self.successors],
        }


class SemanticRegistry:
    """集中维护跨节点字段的业务语义和边界规则。

    Pydantic DTO 负责类型/形状校验，Registry 负责解释字段在流程中的
    meaning、source、consumer 以及不可变的 value rules。别名仍只在
    wire boundary 归一化；持久化对象和 Agent 输入始终使用 canonical name。
    """

    def __init__(
        self,
        common_fields: Mapping[str, SemanticFieldSpec] | None = None,
        node_fields: Mapping[str, Mapping[str, SemanticFieldSpec]] | None = None,
    ) -> None:
        self._common: dict[str, SemanticFieldSpec] = dict(common_fields or {})
        self._node: dict[str, dict[str, SemanticFieldSpec]] = {
            node_type: dict(fields) for node_type, fields in (node_fields or {}).items()
        }

    @classmethod
    def default(cls) -> "SemanticRegistry":
        """Return the built-in registry used by the control plane."""
        registry = cls(_COMMON_FIELDS)
        registry.register_node_fields(
            "architecture_agent",
            {
                "purpose": SemanticFieldSpec(
                    "模块为用户或业务提供的明确价值及边界",
                    "ArchitectureBlueprint/ModuleDesign",
                    "ArchitectureAgent/Planner",
                    False,
                    ("必须区别于技术职责；不能凭空增加需求",),
                ),
                "depends_on_modules": SemanticFieldSpec(
                    "模块之间的业务依赖；值只能是 Blueprint 中已声明的 module_id",
                    "ArchitectureBlueprint",
                    "DynamicPlanBuilder/ArchitectureIntegration",
                    False,
                    ("不得填写 work_item_id、interface_id 或文件路径；依赖图必须无环",),
                ),
                "layers": SemanticFieldSpec(
                    "系统分层及每层允许/禁止依赖",
                    "Architecture Contract",
                    "Policy/ContractCompiler",
                    True,
                    ("层名必须稳定且依赖图无环",),
                ),
                "provided_interfaces": SemanticFieldSpec(
                    "跨模块交互的唯一接口定义",
                    "Architecture Contract",
                    "ImplementationContract/Integration",
                    False,
                    ("同 ID 只能有一个定义，冲突必须拒绝",),
                ),
                "consumed_interfaces": SemanticFieldSpec(
                    "对其他模块接口的引用",
                    "Architecture Contract",
                    "ImplementationContract/Integration",
                    False,
                    ("不得携带 owner_unit；必须匹配已定义接口",),
                ),
                "implementation_units": SemanticFieldSpec(
                    "按层和文件所有权拆分的最小实现单元",
                    "Architecture Contract",
                    "TaskCompiler/CodeAgent",
                    True,
                    ("一个 CodeAgent 单元只能拥有完整文件",),
                ),
            },
        )
        registry.register_node_fields(
            "requirement_agent",
            {
                "requirements": SemanticFieldSpec(
                    "可追踪的业务需求及 AC 编号",
                    "User goal/Trace",
                    "Architecture/Review",
                    True,
                    ("每项必须有验收标准，闲聊内容不得伪装成需求",),
                ),
                "external_documentation": SemanticFieldSpec(
                    "需求显式声明的外部规范主题",
                    "Requirement metadata",
                    "CapabilityGate",
                    False,
                    ("未声明时不得自行请求 external_documentation",),
                ),
            },
        )
        registry.register_node_fields(
            "code_integration_agent",
            {
                "changed_files": SemanticFieldSpec(
                    "候选合并实际产生的文件变更集合",
                    "Git/index diff",
                    "DeliveryValidator/Review",
                    True,
                    ("必须从真实 diff 推导，不能由模型声称",),
                ),
                "merge_report": SemanticFieldSpec(
                    "仅描述合并结果、冲突和适配，不新增业务功能",
                    "Integration tool",
                    "QualityGate",
                    True,
                    ("不得创建未在合同中授权的功能文件",),
                ),
            },
        )
        return registry

    def register(self, name: str, spec: SemanticFieldSpec) -> None:
        """Register a shared field; duplicate names are rejected."""
        self._validate_name(name)
        if not isinstance(spec, SemanticFieldSpec):
            raise TypeError("语义字段必须是 SemanticFieldSpec")
        if name in self._common:
            raise ValueError(f"语义字段 '{name}' 已注册")
        self._common[name] = spec

    def register_node_fields(
        self, node_type: str, fields: Mapping[str, SemanticFieldSpec]
    ) -> None:
        self._validate_name(node_type)
        if not isinstance(fields, Mapping):
            raise TypeError("节点语义字段必须是 Mapping")
        target = self._node.get(node_type, {})
        pending: dict[str, SemanticFieldSpec] = {}
        for name, spec in fields.items():
            self._validate_name(name)
            if not isinstance(spec, SemanticFieldSpec):
                raise TypeError("语义字段必须是 SemanticFieldSpec")
            if name in self._common or name in target or name in pending:
                raise ValueError(f"节点 '{node_type}' 的语义字段 '{name}' 已注册")
            pending[name] = spec
        self._node[node_type] = {**target, **pending}

    def get(self, name: str, node_type: str | None = None) -> SemanticFieldSpec | None:
        if node_type is not None:
            spec = self._node.get(node_type, {}).get(name)
            if spec is not None:
                return spec
        return self._common.get(name)

    def fields_for(self, node_type: str | None = None) -> dict[str, SemanticFieldSpec]:
        fields = dict(self._common)
        if node_type is not None:
            fields.update(self._node.get(node_type, {}))
        return fields

    def validate_blueprint(self, blueprint: Any) -> tuple[str, ...]:
        """Validate Blueprint relationships that a JSON schema cannot express.

        Layer dependency validation (allowed_dependencies must reference declared layers)
        has been moved to the pydantic model layer (ArchitectureBlueprint.validate_unique_ids)
        for earlier failure and automatic retry integration.
        """
        errors: list[str] = []
        modules = tuple(getattr(blueprint, "modules", ()) or ())
        module_ids = [str(getattr(item, "module_id", "")).strip() for item in modules]
        if any(not value for value in module_ids):
            errors.append("Blueprint.modules.module_id 不能为空")
        if len(module_ids) != len(set(module_ids)):
            errors.append("Blueprint.modules.module_id 必须唯一")
        known = set(module_ids)
        for module in modules:
            module_id = str(getattr(module, "module_id", "")).strip()
            purpose = str(getattr(module, "purpose", "") or "").strip()
            if not purpose:
                errors.append(f"模块 {module_id or '<unknown>'} 缺少 purpose")
            dependencies = tuple(getattr(module, "depends_on_modules", ()) or ())
            unknown = sorted(set(dependencies) - known)
            if unknown:
                errors.append(
                    f"模块 {module_id or '<unknown>'} 依赖未声明 module_id: {', '.join(unknown)}"
                )
            if module_id in dependencies:
                errors.append(f"模块 {module_id} 不能依赖自身")
        layers = tuple(getattr(blueprint, "layers", ()) or ())
        layer_ids = [str(getattr(layer, "name", "")).strip() for layer in layers]
        if len(layer_ids) != len(set(layer_ids)):
            errors.append("Blueprint.layers.name 必须唯一")
        # Layer dependency validation removed: now handled by ArchitectureBlueprint.validate_unique_ids
        return tuple(errors)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("语义注册名称不能为空")


def default_semantic_registry() -> SemanticRegistry:
    """Build the control-plane registry with the built-in field catalogue."""
    return SemanticRegistry.default()


_COMMON_FIELDS: dict[str, SemanticFieldSpec] = {
    "goal": SemanticFieldSpec(
        "当前 Trace 的需求目标快照", "TraceContext/ExecutionPlan", "所有节点", True
    ),
    "objective": SemanticFieldSpec(
        "本 WorkItem 必须完成的单一职责", "WorkItem", "当前 Agent", True,
        ("不得扩展为其他节点职责",),
    ),
    "stage_id": SemanticFieldSpec(
        "流程中的生命周期阶段角色；不包含执行权限", "ProcessDefinition/WorkItem", "当前 Agent/GraphRunner", False,
        ("只能使用已注册阶段；执行模式和授权由控制面编译",),
    ),
    "dependencies": SemanticFieldSpec(
        "执行顺序上的前置 WorkItem 摘要；值只能是 work_item_id", "ExecutionPlan", "调度器", False,
        ("不得填写 interface_id 或文件路径",),
    ),
    "input_refs": SemanticFieldSpec(
        "可按 ref_id 读取的已发布产物引用，不包含正文", "ArtifactRepository", "当前 Agent", False,
        ("只能读取列出的 ref_id",),
    ),
    "acceptance_criteria": SemanticFieldSpec(
        "完成判定条件及可验证证据", "WorkItem/Requirement", "QualityGate/Review", True,
        ("每项必须可观察、可测试",),
    ),
    "constraints": SemanticFieldSpec(
        "当前节点必须遵守的硬约束", "WorkItem/Policy", "当前 Agent", False,
        ("不能通过重试删除约束",),
    ),
    "allowed_paths": SemanticFieldSpec(
        "当前节点可写入的路径模式", "WorkItem contract", "ToolGateway", False,
        ("不得越出授权目录",),
    ),
    "required_paths": SemanticFieldSpec(
        "当前节点必须实际交付的具体文件", "ImplementationContract", "DeliveryValidator", False,
        ("只能是具体文件，不能是目录或 glob",),
    ),
    "owned_files": SemanticFieldSpec(
        "当前 CodeAgent 独占写入的完整文件所有权", "ImplementationContract", "CodeAgent/Integration", False,
        ("CodeAgent 实现单元通常恰好一个文件",),
    ),
    "output": SemanticFieldSpec(
        "节点完成后发布的产物实例及其落盘目标", "WorkItem", "下游节点/ArtifactRepository", True,
        ("不得伪造 changed_files 或 digest",),
    ),
}


def compile_node_contract(
    state: Any,
    item: Any,
    registry: SemanticRegistry | None = None,
) -> NodeExecutionContract:
    """Compile semantic definitions and immediate lineage for one WorkItem.

    ``registry`` is injectable for tests and future project-specific semantic
    extensions.  The default remains the immutable built-in registry, so this
    change does not alter the existing task input wire shape.
    """
    semantic_registry = registry or default_semantic_registry()
    fields = semantic_registry.fields_for(str(getattr(item, "agent_id", "")))
    if getattr(item, "implementation_unit_id", None):
        fields.update({
            "implementation_unit_id": SemanticFieldSpec(
                "Architecture 分配的实现单元标识", "ImplementationContract", "CodeAgent", True,
                ("不可由 Agent 重命名或重新拆分",),
            ),
            "depends_on_units": SemanticFieldSpec(
                "实现单元之间的执行依赖；只能引用 unit_id", "ArchitectureContract", "WaveScheduler", False,
                ("只能引用 unit_id；接口 ID 必须通过 interface owner 转换后再进入此字段",),
            ),
            "provides_interfaces": SemanticFieldSpec(
                "本单元定义并拥有的接口声明", "ArchitectureContract", "Integration", False,
                ("必须填写 owner_unit；同一 interface_id 只能有一个提供方",),
            ),
            "consumes_interfaces": SemanticFieldSpec(
                "本单元对外部接口的引用", "ArchitectureContract", "Integration", False,
                ("只能引用已存在的 interface_id；不得填写 owner 字段",),
            ),
        })
    agent_id = str(getattr(item, "agent_id", ""))
    predecessors = []
    successors = []
    plan = getattr(state, "plan", None)
    if plan is not None:
        for dep in getattr(item, "dependencies", ()):
            predecessor = plan.work_item(dep.work_item_id)
            if predecessor is not None:
                predecessors.append({"work_item_id": predecessor.id, "agent_id": predecessor.agent_id, "artifact_key": predecessor.artifact_key})
        for candidate in getattr(plan, "work_items", ()):
            if item.id in candidate.dependency_ids:
                successors.append({"work_item_id": candidate.id, "agent_id": candidate.agent_id, "artifact_key": candidate.artifact_key})
    purpose = f"{item.agent_id} 仅负责：{item.objective}"
    invariants = (
        "控制面字段由系统生成，Agent 不得修改 trace_id、work_item_id、contract_digest 或权限范围",
        "结构合法不等于语义合法；所有 ID 必须符合字段定义及其消费者约束",
    )
    return NodeExecutionContract(
        node_type=str(item.agent_id),
        node_purpose=purpose,
        fields=fields,
        invariants=invariants,
        predecessors=tuple(predecessors),
        successors=tuple(successors),
    )


def validate_task_input_semantics(package: Any) -> tuple[str, ...]:
    """Validate the semantic relationships that JSON Schema cannot express."""
    errors: list[str] = []
    contract = getattr(package, "semantic_contract", None)
    if contract is None:
        errors.append("semantic_contract 缺失")
    else:
        dependency_ids = {item.work_item_id for item in getattr(package, "dependencies", ())}
        predecessor_ids = {item.get("work_item_id") for item in contract.predecessors}
        if dependency_ids != predecessor_ids:
            errors.append("semantic_contract.predecessors 必须与 dependencies 一致")
        if any(not item.get("work_item_id") for item in contract.predecessors + contract.successors):
            errors.append("局部链路节点必须包含 work_item_id")
    # ``expected_paths`` may intentionally be a scope pattern for non-code
    # artifacts; concrete-file semantics are enforced on implementation
    # ``required_paths``/``owned_files`` below.
    implementation = getattr(package, "implementation", None)
    if implementation:
        owned = tuple(implementation.get("owned_files") or ())
        if any(any(token in path for token in ("*", "?", "[", "]")) for path in owned):
            errors.append("implementation.owned_files 只能包含具体文件路径")
        if any(path in {"*", "**"} for path in implementation.get("forbidden_paths", ())):
            errors.append("implementation.forbidden_paths 不得使用全局通配符")
    return tuple(errors)


def coalesce_alias(
    payload: dict[str, Any],
    canonical: str,
    *aliases: str,
    default: Any = None,
) -> Any:
    """Return one canonical value and reject conflicting aliases.

    This deliberately treats ``None`` and an absent key as unspecified, while
    preserving meaningful false-y values such as ``[]`` and ``""``.  The
    caller may then remove aliases before serializing the object.
    """
    values: list[tuple[str, Any]] = []
    for name in (canonical, *aliases):
        if name in payload and payload[name] is not None and payload[name] not in ([], ""):
            values.append((name, payload[name]))
    if not values:
        return default
    first = values[0][1]
    for name, value in values[1:]:
        if value != first:
            names = ", ".join(item[0] for item in values)
            raise ValueError(f"{names} 表示同一语义但值不一致")
    return first


def normalize_aliases(
    payload: Any,
    mapping: dict[str, tuple[str, ...]],
) -> Any:
    """Copy a wire object and map all accepted aliases to canonical keys."""
    if not isinstance(payload, dict):
        return payload
    data = dict(payload)
    for canonical, aliases in mapping.items():
        value = coalesce_alias(data, canonical, *aliases)
        if value is not None:
            data[canonical] = value
        for alias in aliases:
            data.pop(alias, None)
    return data


__all__ = [
    "coalesce_alias",
    "normalize_aliases",
    "SemanticFieldSpec",
    "SemanticRegistry",
    "default_semantic_registry",
    "NodeExecutionContract",
    "compile_node_contract",
    "validate_task_input_semantics",
]

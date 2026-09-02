"""分层架构设计的标准化对象协议。

架构设计分为三个固定深度：

* 0 - ``ArchitectureBlueprint``：系统边界和全局决策；
* 1 - ``ModuleDesign``：单个模块的职责、依赖和协作接口；
* 2 - ``ImplementationDesign``：接口实现、文件 ownership 和测试边界。

这些对象是架构 Agent 之间的传递合同。Markdown 只作为人类可读投影，
不能作为下一层判断的事实来源。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.architecture.contract_input import (
    ContractEntrypointInput,
    ContractImplementationUnitInput,
    ContractInterfaceInput,
    ConsumedInterfaceRefInput,
)


class _DesignModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class LayerDecision(_DesignModel):
    name: str = Field(min_length=1, max_length=64)
    allowed_dependencies: list[str] = Field(default_factory=list, max_length=32)
    forbidden_imports: list[str] = Field(default_factory=list, max_length=64)
    path_mapping: list[str] = Field(default_factory=list, max_length=32)


class ModuleRef(_DesignModel):
    module_id: str = Field(min_length=1, max_length=128)
    responsibility: str = Field(min_length=1, max_length=500)
    # Business intent is kept separate from implementation responsibilities so
    # dynamic planning can preserve the value boundary of each module.
    purpose: str | None = Field(
        default=None,
        max_length=1000,
        description="模块为用户或业务提供的价值及边界，不是技术职责列表",
    )
    depends_on_modules: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="只引用 Blueprint 中已声明的 module_id",
    )
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_dependencies(cls, value: object) -> object:
        if not isinstance(value, dict) or "dependencies" not in value:
            return value
        data = dict(value)
        if "depends_on_modules" not in data:
            data["depends_on_modules"] = data.get("dependencies") or []
        data.pop("dependencies", None)
        return data

    @model_validator(mode="after")
    def normalize_business_purpose(self) -> "ModuleRef":
        if not self.purpose:
            object.__setattr__(self, "purpose", self.responsibility)
        if self.module_id in self.depends_on_modules:
            raise ValueError("ModuleRef 不能依赖自身")
        if len(set(self.depends_on_modules)) != len(self.depends_on_modules):
            raise ValueError("ModuleRef.depends_on_modules 不能重复")
        return self


class InterfaceRef(_DesignModel):
    interface_id: str = Field(min_length=1, max_length=128)
    direction: Literal["provided", "consumed"]
    summary: str = Field(min_length=1, max_length=500)


class ArchitectureBlueprint(_DesignModel):
    schema_version: Literal[1]
    design_id: str = Field(min_length=1, max_length=128)
    depth: Literal[0] = 0
    system_boundary: str = Field(min_length=1, max_length=2000)
    layers: list[LayerDecision] = Field(min_length=1, max_length=32)
    modules: list[ModuleRef] = Field(min_length=1, max_length=128)
    global_constraints: list[str] = Field(default_factory=list, max_length=64)
    runtime_profile: str | None = Field(default=None, max_length=128)
    entrypoints: ContractEntrypointInput = Field(default_factory=ContractEntrypointInput)
    required_files: list[str] = Field(default_factory=list, max_length=128)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ArchitectureBlueprint":
        layer_ids = [item.name for item in self.layers]
        module_ids = [item.module_id for item in self.modules]
        if len(layer_ids) != len(set(layer_ids)):
            raise ValueError("ArchitectureBlueprint.layers 不能重复")
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("ArchitectureBlueprint.modules 不能重复")
        for path in self.required_files:
            normalized = path.replace("\\", "/").strip()
            if not normalized or normalized.endswith("/") or any(token in normalized for token in ("*", "?", "[", "]")):
                raise ValueError("ArchitectureBlueprint.required_files 必须是具体文件路径")
        return self


class ModuleDesign(_DesignModel):
    schema_version: Literal[1]
    design_id: str = Field(min_length=1, max_length=128)
    depth: Literal[1] = 1
    parent_design_id: str = Field(min_length=1, max_length=128)
    module_id: str = Field(min_length=1, max_length=128)
    purpose: str | None = Field(
        default=None,
        max_length=1000,
        description="必须保留对应 Blueprint 模块的业务目的",
    )
    responsibilities: list[str] = Field(min_length=1, max_length=64)
    provided_interfaces: list[InterfaceRef] = Field(default_factory=list, max_length=64)
    consumed_interfaces: list[InterfaceRef] = Field(default_factory=list, max_length=64)
    entities: list[str] = Field(default_factory=list, max_length=64)
    depends_on_modules: list[str] = Field(
        default_factory=list,
        max_length=64,
        description="与 Blueprint 模块依赖保持一致",
    )
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=64)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "depends_on_modules" not in data and "dependencies" in data:
            data["depends_on_modules"] = data.get("dependencies") or []
        data.pop("dependencies", None)
        return data

    @model_validator(mode="after")
    def validate_interface_directions(self) -> "ModuleDesign":
        if any(item.direction != "provided" for item in self.provided_interfaces):
            raise ValueError("provided_interfaces 中的 direction 必须为 provided")
        if any(item.direction != "consumed" for item in self.consumed_interfaces):
            raise ValueError("consumed_interfaces 中的 direction 必须为 consumed")
        if self.module_id in self.depends_on_modules:
            raise ValueError("ModuleDesign 不能依赖自身")
        if len(set(self.depends_on_modules)) != len(self.depends_on_modules):
            raise ValueError("ModuleDesign.depends_on_modules 不能重复")
        return self


class ImplementationDesign(_DesignModel):
    schema_version: Literal[1]
    design_id: str = Field(min_length=1, max_length=128)
    depth: Literal[2] = 2
    parent_design_id: str = Field(min_length=1, max_length=128)
    module_id: str = Field(min_length=1, max_length=128)
    provided_interfaces: list[ContractInterfaceInput] = Field(default_factory=list, max_length=128)
    consumed_interfaces: list[ConsumedInterfaceRefInput] = Field(default_factory=list, max_length=128)
    implementation_units: list[ContractImplementationUnitInput] = Field(
        min_length=1, max_length=256
    )
    required_test_types: list[str] = Field(default_factory=list, max_length=32)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_units_belong_to_module(self) -> "ImplementationDesign":
        unit_ids = {item.unit_id for item in self.implementation_units}
        for unit in self.implementation_units:
            if len(unit.owned_files) != 1:
                raise ValueError(
                    f"实现单元 {unit.unit_id} 必须且只能负责一个具体 owned_file"
                )
        for interface in self.provided_interfaces:
            if interface.owner_unit not in unit_ids:
                raise ValueError(
                    f"接口 {interface.interface_id} 的 owner_unit 不属于模块 {self.module_id}"
                )
        provided_ids = [item.interface_id for item in self.provided_interfaces]
        if len(provided_ids) != len(set(provided_ids)):
            raise ValueError("provided_interfaces 不能包含重复 interface_id")
        consumed_ids = [item.interface_id for item in self.consumed_interfaces]
        if len(consumed_ids) != len(set(consumed_ids)):
            raise ValueError("consumed_interfaces 不能包含重复 interface_id")
        return self

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_interfaces(cls, value: object) -> object:
        """Accept the old mixed array only at the wire boundary.

        Persisted and validated objects never retain ``interfaces``.  Legacy
        entries marked consumed become references; all other entries remain
        provider declarations for one migration cycle.
        """
        if not isinstance(value, dict) or "interfaces" not in value:
            return value
        data = dict(value)
        legacy = data.pop("interfaces") or []
        provided = list(data.get("provided_interfaces") or [])
        consumed = list(data.get("consumed_interfaces") or [])
        for raw in legacy:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).lower()
            direction = str(raw.get("direction", "")).lower()
            if direction == "consumed" or "consum" in kind:
                consumed.append({
                    "interface_id": raw.get("interface_id", raw.get("id", "")),
                    "usage": raw.get("summary") or raw.get("name"),
                })
            else:
                provided.append(raw)
        data["provided_interfaces"] = provided
        data["consumed_interfaces"] = consumed
        return data


class ArchitectureDesignBundle(_DesignModel):
    """Integration 后的完整架构对象，深度固定为 3 层以内。"""

    schema_version: Literal[1]
    blueprint: ArchitectureBlueprint
    modules: list[ModuleDesign] = Field(min_length=1, max_length=128)
    implementations: list[ImplementationDesign] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> "ArchitectureDesignBundle":
        module_refs = {item.module_id for item in self.blueprint.modules}
        modules_by_id: dict[str, ModuleDesign] = {}
        for design in self.modules:
            if design.parent_design_id != self.blueprint.design_id:
                raise ValueError(f"模块设计 {design.design_id} 未引用当前总体蓝图")
            if design.module_id not in module_refs:
                raise ValueError(f"模块设计 {design.module_id} 不在总体蓝图模块清单中")
            if design.module_id in modules_by_id:
                raise ValueError(f"模块 {design.module_id} 存在多个模块设计")
            blueprint_ref = next(
                item for item in self.blueprint.modules if item.module_id == design.module_id
            )
            if design.purpose is not None and design.purpose != blueprint_ref.purpose:
                raise ValueError(
                    f"模块设计 {design.module_id} 的 purpose 必须与 Blueprint 一致"
                )
            if set(design.depends_on_modules) != set(blueprint_ref.depends_on_modules):
                raise ValueError(
                    f"模块设计 {design.module_id} 的 depends_on_modules 必须与 Blueprint 一致"
                )
            modules_by_id[design.module_id] = design

        # Resolve business-level module dependencies before accepting any
        # lower-level implementation objects.  The dependency graph is owned
        # by the Blueprint; ModuleDesign only refines its target module.
        module_refs_by_id = {item.module_id: item for item in self.blueprint.modules}
        module_edges = {
            module_id: set(module_ref.depends_on_modules)
            for module_id, module_ref in module_refs_by_id.items()
        }
        for module_id, dependencies in module_edges.items():
            unknown = sorted(dependencies - set(module_refs_by_id))
            if unknown:
                raise ValueError(
                    f"模块 {module_id} 依赖不存在的模块: {', '.join(unknown)}"
                )
        pending = {key: set(value) for key, value in module_edges.items()}
        resolved: set[str] = set()
        while pending:
            ready = {key for key, value in pending.items() if value <= resolved}
            if not ready:
                raise ValueError("ArchitectureBlueprint 模块依赖存在循环")
            resolved.update(ready)
            for key in ready:
                pending.pop(key)

        interface_ids: set[str] = set()
        for design in self.implementations:
            if design.parent_design_id not in {item.design_id for item in self.modules}:
                raise ValueError(f"实现设计 {design.design_id} 未引用已存在的模块设计")
            if design.module_id not in modules_by_id:
                raise ValueError(f"实现设计 {design.module_id} 没有对应模块设计")
            for interface in design.provided_interfaces:
                if interface.interface_id in interface_ids:
                    raise ValueError(f"接口 {interface.interface_id} 在多个实现设计中重复")
                interface_ids.add(interface.interface_id)
        for design in self.implementations:
            unknown = sorted(
                {item.interface_id for item in design.consumed_interfaces} - interface_ids
            )
            if unknown:
                raise ValueError(
                    f"实现设计 {design.module_id} 引用了未声明的接口: {', '.join(unknown)}"
                )
        missing = sorted(module_refs - set(modules_by_id))
        if missing:
            raise ValueError("缺少模块设计: " + ", ".join(missing))
        missing_impl = sorted(module_refs - {item.module_id for item in self.implementations})
        if missing_impl:
            raise ValueError("缺少实现设计: " + ", ".join(missing_impl))
        # Integration is the boundary between module-level design and code
        # waves.  Unit ids and file ownership must therefore be globally
        # unique, and a unit may only depend on an earlier wave.  This makes
        # the merge order deterministic and prevents same-wave imports from
        # being mistaken for an available interface.
        units = [unit for design in self.implementations for unit in design.implementation_units]
        unit_ids = [unit.unit_id for unit in units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("实现单元 unit_id 在不同模块之间不能重复")
        by_id = {unit.unit_id: unit for unit in units}
        owned_files: dict[str, str] = {}
        for unit in units:
            for path in unit.owned_files:
                normalized = path.replace("\\", "/").lstrip("/")
                previous = owned_files.get(normalized)
                if previous is not None and previous != unit.unit_id:
                    raise ValueError(f"文件 {normalized} 被多个实现单元拥有: {previous}, {unit.unit_id}")
                owned_files[normalized] = unit.unit_id
            for required in unit.required_paths:
                normalized = required.replace("\\", "/").lstrip("/")
                if normalized not in {p.replace("\\", "/").lstrip("/") for p in unit.owned_files}:
                    raise ValueError(f"实现单元 {unit.unit_id} 的 required_paths 必须属于 owned_files: {required}")
        for unit in units:
            current_wave = unit.wave if unit.wave is not None else 0
            unknown = sorted(set(unit.depends_on) - set(by_id))
            if unknown:
                raise ValueError(
                    f"实现单元 {unit.unit_id} 依赖不存在的实现单元: {', '.join(unknown)}"
                )
            for dependency in unit.depends_on:
                dep_wave = by_id[dependency].wave if by_id[dependency].wave is not None else 0
                if dep_wave >= current_wave:
                    raise ValueError(
                        f"实现单元 {unit.unit_id} 必须依赖更早 wave；"
                        f"{dependency} 为 wave={dep_wave}, 当前为 wave={current_wave}"
                    )
        return self

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def to_project_contract(self) -> dict[str, object]:
        """将三层对象转换成唯一 ProjectContract 的 wire 形态。"""
        layers = [item.name for item in self.blueprint.layers]
        payload: dict[str, object] = {
            "schema_version": 1,
            "layers": [item.model_dump(mode="json") for item in self.blueprint.layers],
            "required_test_types": sorted(
                {
                    test_type
                    for item in self.implementations
                    for test_type in item.required_test_types
                }
            ),
            "entrypoints": self.blueprint.entrypoints.model_dump(exclude_none=True),
            "required_files": list(self.blueprint.required_files),
            "interfaces": [],
            "implementation_units": [],
        }
        # ContractInput expects layers as objects; ArchitectureService will
        # normalize this to the canonical layer mapping before persistence.
        implementations = sorted(
            self.implementations,
            key=lambda item: item.module_id,
        )
        for implementation in implementations:
            for interface in implementation.provided_interfaces:
                payload["interfaces"].append(interface.model_dump(mode="json"))  # type: ignore[union-attr]
            for unit in sorted(
                implementation.implementation_units,
                key=lambda item: (item.wave if item.wave is not None else 0, item.unit_id),
            ):
                payload["implementation_units"].append(unit.model_dump(mode="json"))  # type: ignore[union-attr]
        return payload


def parse_design(value: dict[str, object] | str) -> ArchitectureBlueprint | ModuleDesign | ImplementationDesign:
    """按 ``depth`` 将 JSON 输入解析为唯一的架构设计对象。"""
    import json

    raw = json.loads(value) if isinstance(value, str) else value
    if not isinstance(raw, dict):
        raise TypeError("架构设计对象必须是 JSON object")
    depth = raw.get("depth")
    model = {0: ArchitectureBlueprint, 1: ModuleDesign, 2: ImplementationDesign}.get(depth)
    if model is None:
        raise ValueError("架构设计 depth 只能是 0、1 或 2")
    return model.model_validate(raw)


__all__ = [
    "ArchitectureBlueprint",
    "ArchitectureDesignBundle",
    "ImplementationDesign",
    "InterfaceRef",
    "LayerDecision",
    "ModuleDesign",
    "ModuleRef",
    "parse_design",
]

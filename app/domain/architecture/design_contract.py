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
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)


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
    responsibilities: list[str] = Field(min_length=1, max_length=64)
    provided_interfaces: list[InterfaceRef] = Field(default_factory=list, max_length=64)
    consumed_interfaces: list[InterfaceRef] = Field(default_factory=list, max_length=64)
    entities: list[str] = Field(default_factory=list, max_length=64)
    dependencies: list[str] = Field(default_factory=list, max_length=64)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=64)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_interface_directions(self) -> "ModuleDesign":
        if any(item.direction != "provided" for item in self.provided_interfaces):
            raise ValueError("provided_interfaces 中的 direction 必须为 provided")
        if any(item.direction != "consumed" for item in self.consumed_interfaces):
            raise ValueError("consumed_interfaces 中的 direction 必须为 consumed")
        return self


class ImplementationDesign(_DesignModel):
    schema_version: Literal[1]
    design_id: str = Field(min_length=1, max_length=128)
    depth: Literal[2] = 2
    parent_design_id: str = Field(min_length=1, max_length=128)
    module_id: str = Field(min_length=1, max_length=128)
    interfaces: list[ContractInterfaceInput] = Field(default_factory=list, max_length=128)
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
        for interface in self.interfaces:
            if interface.owner_unit not in unit_ids:
                raise ValueError(
                    f"接口 {interface.interface_id} 的 owner_unit 不属于模块 {self.module_id}"
                )
        return self


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
            modules_by_id[design.module_id] = design

        interface_ids: set[str] = set()
        for design in self.implementations:
            if design.parent_design_id not in {item.design_id for item in self.modules}:
                raise ValueError(f"实现设计 {design.design_id} 未引用已存在的模块设计")
            if design.module_id not in modules_by_id:
                raise ValueError(f"实现设计 {design.module_id} 没有对应模块设计")
            for interface in design.interfaces:
                if interface.interface_id in interface_ids:
                    raise ValueError(f"接口 {interface.interface_id} 在多个实现设计中重复")
                interface_ids.add(interface.interface_id)
        missing = sorted(module_refs - set(modules_by_id))
        if missing:
            raise ValueError("缺少模块设计: " + ", ".join(missing))
        missing_impl = sorted(module_refs - {item.module_id for item in self.implementations})
        if missing_impl:
            raise ValueError("缺少实现设计: " + ", ".join(missing_impl))
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
        for implementation in self.implementations:
            for interface in implementation.interfaces:
                payload["interfaces"].append(interface.model_dump(mode="json"))  # type: ignore[union-attr]
            for unit in implementation.implementation_units:
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

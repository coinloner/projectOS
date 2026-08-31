"""Architecture Contract 工具的结构化输入 DTO。

工具调用使用固定字段的对象/数组，而不是把整个合同塞进一个 JSON 字符串。
DTO 只负责 wire 形状、基础类型和归一化；最终的跨字段约束仍由
``ImplementationContract`` 统一校验。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ContractModel(BaseModel):
    """Closed wire object shared by all contract DTOs."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ContractLayerInput(_ContractModel):
    """One architecture layer and its local policy projection."""

    name: str = Field(min_length=1, max_length=64)
    allowed_dependencies: list[str] = Field(default_factory=list, max_length=32)
    forbidden_imports: list[str] = Field(default_factory=list, max_length=64)
    path_mapping: list[str] = Field(default_factory=list, max_length=32)


class ContractEntrypointInput(_ContractModel):
    backend_file: str | None = Field(default=None, max_length=512)
    backend_import: str | None = Field(default=None, max_length=256)
    backend_command: str | None = Field(default=None, max_length=512)
    frontend_file: str | None = Field(default=None, max_length=512)
    health_path: str = Field(default="/health", min_length=1, max_length=256)


class ContractInterfaceInput(_ContractModel):
    interface_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    owner_unit: str = Field(min_length=1, max_length=128)
    owner_file: str | None = Field(default=None, max_length=512)
    signature: str | None = Field(default=None, max_length=1000)
    input_schema: str | None = Field(default=None, max_length=4000)
    output_schema: str | None = Field(default=None, max_length=4000)
    errors: list[str] = Field(default_factory=list, max_length=32)
    constraints: list[str] = Field(default_factory=list, max_length=32)


class ContractImplementationUnitInput(_ContractModel):
    """One complete-file implementation scope.

    ``required_files`` is the public wire name.  ``required_paths`` remains an
    accepted compatibility alias during migration; the DTO rejects conflicting
    values so the canonical contract never has two different meanings.
    """

    unit_id: str = Field(min_length=1, max_length=128)
    layer: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=1000)
    allowed_paths: list[str] = Field(min_length=1, max_length=64)
    required_files: list[str] = Field(default_factory=list, max_length=64)
    required_paths: list[str] = Field(default_factory=list, max_length=64)
    forbidden_paths: list[str] = Field(default_factory=list, max_length=64)
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    input_refs: list[str] = Field(default_factory=lambda: ["architecture", "environment"], max_length=64)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=32)
    constraints: list[str] = Field(default_factory=list, max_length=32)
    non_goals: list[str] = Field(default_factory=list, max_length=32)
    policy_refs: list[str] = Field(default_factory=list, max_length=32)
    skill_refs: list[str] = Field(default_factory=list, max_length=32)
    parallel_group: str | None = Field(default=None, max_length=128)
    output_key: str | None = Field(default=None, max_length=128)
    slot: str | None = Field(default=None, max_length=64)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)
    wave: int | None = Field(default=None, ge=0, le=1000)
    owned_files: list[str] = Field(default_factory=list, max_length=64)
    provides_interfaces: list[str] = Field(default_factory=list, max_length=64)
    consumes_interfaces: list[str] = Field(default_factory=list, max_length=64)
    provided_symbols: list[str] = Field(default_factory=list, max_length=128)
    required_symbols: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def normalize_required_alias(self) -> "ContractImplementationUnitInput":
        if self.required_files and self.required_paths and self.required_files != self.required_paths:
            raise ValueError("required_files 与 required_paths 不能表示不同文件")
        if not self.required_paths and self.required_files:
            self.required_paths = list(self.required_files)
        return self


class ProjectContractInput(_ContractModel):
    """唯一 Project Contract 的结构化工具输入。"""

    schema_version: Literal[1]
    layers: list[ContractLayerInput] = Field(min_length=1, max_length=64)
    required_test_types: list[str] = Field(max_length=32)
    entrypoints: ContractEntrypointInput
    required_files: list[str] = Field(max_length=256)
    interfaces: list[ContractInterfaceInput] = Field(max_length=256)
    implementation_units: list[ContractImplementationUnitInput] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_unique_layers(self) -> "ProjectContractInput":
        names = [layer.name for layer in self.layers]
        if len(names) != len(set(names)):
            raise ValueError("layers 不能包含重复 name")
        return self

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert the wire array shape to the internal canonical contract."""

        layer_names = [layer.name for layer in self.layers]
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "layers": layer_names,
            "allowed_dependencies": {
                layer.name: list(layer.allowed_dependencies) for layer in self.layers
            },
            "forbidden_imports": {
                layer.name: list(layer.forbidden_imports) for layer in self.layers
            },
            "path_mapping": {
                layer.name: list(layer.path_mapping) for layer in self.layers
            },
            "required_test_types": list(self.required_test_types),
            "entrypoints": self.entrypoints.model_dump(exclude_none=True),
            "required_files": list(self.required_files),
            "interfaces": [interface.model_dump(exclude_none=True) for interface in self.interfaces],
            "implementation_units": [],
        }
        for unit in self.implementation_units:
            item = unit.model_dump(exclude_none=True)
            # The internal parser uses required_paths as its canonical field.
            item.pop("required_files", None)
            payload["implementation_units"].append(item)
        return payload

    @classmethod
    def from_wire(cls, value: "ProjectContractInput | dict[str, Any]") -> "ProjectContractInput":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("contract 必须是 Project Contract 对象")
        return cls.model_validate(value)


__all__ = [
    "ContractEntrypointInput",
    "ContractImplementationUnitInput",
    "ContractInterfaceInput",
    "ContractLayerInput",
    "ProjectContractInput",
]

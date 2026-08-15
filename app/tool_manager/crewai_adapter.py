"""ProjectOS 工具注册记录到 CrewAI 工具的适配层。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model

from app.tool_catalog.catalog import ToolRegistration
from app.tool_manager.source import ToolDef


class ProjectOSTool(BaseTool):
    """把一个已授权的 ProjectOS 工具交给 CrewAI 执行。

    参数校验与 tool-calling loop 属于 CrewAI；本类只保留注册记录并把已验证
    的参数委托给原始 ToolSource。这使本地 ToolSet 和 MCP Source 走同一条
    CrewAI 执行路径。
    """

    _registration: ToolRegistration = PrivateAttr()
    _is_authorized: Callable[[ToolRegistration], bool] = PrivateAttr()

    @classmethod
    def from_registration(
        cls,
        registration: ToolRegistration,
        *,
        is_authorized: Callable[[ToolRegistration], bool],
    ) -> ProjectOSTool:
        tool = cls(
            name=registration.definition.name,
            description=registration.definition.description,
            args_schema=args_schema_for(registration.definition),
        )
        tool._registration = registration
        tool._is_authorized = is_authorized
        return tool

    def _run(self, **arguments: Any) -> str:
        if not self._is_authorized(self._registration):
            raise PermissionError(
                f"工具 '{self._registration.definition.name}' 当前未获授权"
            )
        return str(
            self._registration.source.execute(
                self._registration.definition.name, arguments
            )
        )


def args_schema_for(definition: ToolDef) -> type[BaseModel]:
    """把 ToolDef 的受限 JSON Schema 转为 CrewAI 使用的 Pydantic 模型。

    ToolDef 保持 MCP 通用的 JSON Schema 格式。当前适配器故意只支持 ProjectOS
    已使用的扁平对象参数，避免把不完整的 JSON Schema 静默降级为错误契约。
    后续若需要数组、嵌套对象或联合类型，应显式扩展这里并增加适配测试。
    """
    schema = definition.parameters or {}
    if not isinstance(schema, dict):
        raise ValueError(f"工具 '{definition.name}' 的 parameters 必须是 object")

    _ensure_allowed_keys(
        schema,
        {"type", "properties", "required", "additionalProperties", "description", "title"},
        definition.name,
        "parameters",
    )
    schema_type = schema.get("type", "object")
    if schema_type != "object":
        raise ValueError(f"工具 '{definition.name}' 只支持 object 类型的 parameters")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"工具 '{definition.name}' 的 properties 必须是 object")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(field, str) for field in required
    ):
        raise ValueError(f"工具 '{definition.name}' 的 required 必须是字符串数组")
    required_names = set(required)
    unknown_required = required_names - set(properties)
    if unknown_required:
        names = ", ".join(sorted(unknown_required))
        raise ValueError(f"工具 '{definition.name}' 的 required 引用了未知字段: {names}")

    additional_properties = schema.get("additionalProperties", True)
    if not isinstance(additional_properties, bool):
        raise ValueError(
            f"工具 '{definition.name}' 暂不支持对象形式的 additionalProperties"
        )

    fields: dict[str, tuple[type[Any], Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not field_name:
            raise ValueError(f"工具 '{definition.name}' 包含无效参数名")
        if not isinstance(field_schema, dict):
            raise ValueError(
                f"工具 '{definition.name}' 的参数 '{field_name}' 必须是 object"
            )
        fields[field_name] = _field_for(
            tool_name=definition.name,
            field_name=field_name,
            field_schema=field_schema,
            required=field_name in required_names,
        )

    model_name = f"{_identifier(definition.name)}Arguments"
    return create_model(
        model_name,
        __config__=ConfigDict(extra="allow" if additional_properties else "forbid"),
        **fields,
    )


def _field_for(
    *,
    tool_name: str,
    field_name: str,
    field_schema: dict[str, Any],
    required: bool,
) -> tuple[type[Any], Any]:
    _ensure_allowed_keys(
        field_schema,
        {"type", "description", "default", "title"},
        tool_name,
        f"参数 '{field_name}'",
    )
    json_type = field_schema.get("type")
    python_type = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
    }.get(json_type)
    if python_type is None:
        raise ValueError(
            f"工具 '{tool_name}' 的参数 '{field_name}' 使用了不支持的类型: "
            f"{json_type!r}"
        )

    description = field_schema.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(f"工具 '{tool_name}' 的参数 '{field_name}' 描述必须是字符串")

    if required:
        default: Any = ...
    else:
        default = field_schema.get("default", None)
    return python_type, Field(default=default, description=description)


def _ensure_allowed_keys(
    schema: dict[str, Any], allowed: set[str], tool_name: str, location: str
) -> None:
    unsupported = set(schema) - allowed
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"工具 '{tool_name}' 的 {location} 包含不支持的约束: {names}")


def _identifier(name: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in name) or "Tool"

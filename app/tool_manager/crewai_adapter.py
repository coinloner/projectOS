"""ProjectOS 工具注册记录到 CrewAI 工具的适配层。"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any, Literal, Union

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model

from app.tool_manager.catalog import ToolRegistration
from app.tool_manager.source import ToolDef
from app.execution_context import ExecutionContext


class ProjectOSTool(BaseTool):
    """把一个已授权的 ProjectOS 工具交给 CrewAI 执行。

    参数校验与 tool-calling loop 属于 CrewAI；本类只保留注册记录并把已验证
    的参数委托给原始 ToolSource。这使本地 ToolSet 和 MCP Source 走同一条
    CrewAI 执行路径。
    """

    _registration: ToolRegistration = PrivateAttr()
    _is_authorized: Callable[[ToolRegistration, ExecutionContext | None], bool] = PrivateAttr()
    _context: ExecutionContext | None = PrivateAttr(default=None)

    @classmethod
    def from_registration(
        cls,
        registration: ToolRegistration,
        *,
        is_authorized: Callable[[ToolRegistration, ExecutionContext | None], bool],
        context: ExecutionContext | None = None,
    ) -> ProjectOSTool:
        tool = cls(
            name=registration.definition.name,
            description=registration.definition.description,
            args_schema=args_schema_for(registration.definition),
            result_as_answer=registration.definition.completion_policy == "final",
        )
        tool._registration = registration
        tool._is_authorized = is_authorized
        tool._context = context
        return tool

    def _run(self, **arguments: Any) -> str:
        if not self._is_authorized(self._registration, self._context):
            raise PermissionError(
                f"工具 '{self._registration.definition.name}' 当前未获授权"
            )
        progress = getattr(self._context, "progress", None)
        tool_name = self._registration.definition.name
        if progress is not None:
            progress.tool_started(tool_name)
        try:
            result = str(
                self._registration.source.execute(
                    tool_name, arguments, context=self._context
                )
            )
            if tool_name == "save_implementation_contract":
                _raise_for_contract_failure(result)
        except Exception:
            if progress is not None:
                progress.tool_completed(tool_name, success=False)
            raise
        if progress is not None:
            progress.tool_completed(tool_name)
        return result


def args_schema_for(definition: ToolDef) -> type[BaseModel]:
    """把 ToolDef 的受限 JSON Schema 转为 CrewAI 使用的 Pydantic 模型。

    ToolDef 保持 MCP 通用的 JSON Schema 格式。适配器递归构造嵌套
    Pydantic 模型，确保数组、对象和 nullable 字段在本地校验与 CrewAI 的
    strict function schema 中保持同一语义。
    """
    schema = definition.parameters or {}
    if not isinstance(schema, dict):
        raise ValueError(f"工具 '{definition.name}' 的 parameters 必须是 object")

    definitions = schema.get("$defs", {})
    if definitions and not isinstance(definitions, dict):
        raise ValueError(f"工具 '{definition.name}' 的 $defs 必须是 object")
    root = _resolve_schema(schema, definitions, definition.name)
    if root.get("type", "object") != "object":
        raise ValueError(f"工具 '{definition.name}' 只支持 object 类型的 parameters")
    return _model_for_object(
        root,
        f"{_identifier(definition.name)}Arguments",
        definitions,
        definition.name,
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
        # Required fields stay non-nullable.  CrewAI serializes every tool as
        # an OpenAI ``strict: true`` function, so the schema must agree with
        # the provider about whether a value can be omitted or null.
        return python_type, Field(default=..., description=description)

    # CrewAI's OpenAI adapter calls ``ensure_all_properties_required`` and
    # marks every property as required in the wire schema.  Represent an
    # optional ToolDef field as a required nullable value instead of emitting
    # the invalid combination ``required + type=string + default=null``.
    # Tool wrappers normalize ``None`` to their domain default where needed.
    nullable_type = python_type | None
    default = field_schema.get("default", None)
    return nullable_type, Field(default=default, description=description)


def _model_for_object(
    schema: dict[str, Any],
    model_name: str,
    definitions: dict[str, Any],
    tool_name: str,
) -> type[BaseModel]:
    """Build a closed Pydantic model for an object schema recursively."""
    _ensure_allowed_keys(
        schema,
        {
            "type", "properties", "required", "additionalProperties", "description",
            "title", "$ref", "$defs", "minProperties", "maxProperties",
        },
        tool_name,
        "object schema",
    )
    # Pydantic embeds nested models in a local ``$defs`` block.  Carry those
    # definitions down the recursion so refs remain resolvable even when the
    # object is nested under a tool argument (for example ``contract``).
    local_definitions = dict(definitions)
    embedded = schema.get("$defs", {})
    if isinstance(embedded, dict):
        local_definitions.update(embedded)
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"工具 '{tool_name}' 的 properties 必须是 object")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(field, str) for field in required):
        raise ValueError(f"工具 '{tool_name}' 的 required 必须是字符串数组")
    required_names = set(required)
    unknown_required = required_names - set(properties)
    if unknown_required:
        raise ValueError(f"工具 '{tool_name}' 的 required 引用了未知字段: {', '.join(sorted(unknown_required))}")
    additional_properties = schema.get("additionalProperties", False)
    if not isinstance(additional_properties, bool):
        raise ValueError(f"工具 '{tool_name}' 暂不支持对象形式的 additionalProperties")

    fields: dict[str, tuple[type[Any], Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_name, str) or not field_name:
            raise ValueError(f"工具 '{tool_name}' 包含无效参数名")
        if not isinstance(field_schema, dict):
            raise ValueError(f"工具 '{tool_name}' 的参数 '{field_name}' 必须是 object")
        resolved = _resolve_schema(field_schema, local_definitions, tool_name)
        field_type = _python_type_for_schema(
            resolved,
            definitions=local_definitions,
            tool_name=tool_name,
            model_name=f"{_identifier(model_name)}_{_identifier(field_name)}",
        )
        description = resolved.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError(f"工具 '{tool_name}' 的参数 '{field_name}' 描述必须是字符串")
        if field_name in required_names:
            fields[field_name] = (field_type, Field(default=..., description=description))
            continue
        default = resolved.get("default", None)
        if default is None and resolved.get("type") == "array" and not _allows_null(resolved):
            fields[field_name] = (field_type, Field(default_factory=list, description=description))
        elif default is None and resolved.get("type") == "object" and not _allows_null(resolved):
            fields[field_name] = (field_type, Field(default_factory=field_type, description=description))
        else:
            if not _allows_null(resolved) and field_type is not type(None):
                field_type = field_type | None
            fields[field_name] = (field_type, Field(default=default, description=description))
    return create_model(
        model_name,
        __config__=ConfigDict(extra="allow" if additional_properties else "forbid"),
        **fields,
    )


def _python_type_for_schema(
    schema: dict[str, Any],
    *,
    definitions: dict[str, Any],
    tool_name: str,
    model_name: str,
) -> type[Any]:
    embedded = schema.get("$defs", {})
    if isinstance(embedded, dict) and embedded:
        definitions = {**definitions, **embedded}
    schema = _resolve_schema(schema, definitions, tool_name)
    variants = schema.get("anyOf", schema.get("oneOf"))
    if variants is not None:
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"工具 '{tool_name}' 的联合类型不能为空")
        types = [
            _python_type_for_schema(item, definitions=definitions, tool_name=tool_name, model_name=model_name)
            for item in variants
            if isinstance(item, dict) and item.get("type") != "null"
        ]
        if not types:
            return type(None)
        if len(types) == 1:
            return types[0] | None
        return Union[tuple(types)]  # type: ignore[valid-type]
    json_type = schema.get("type")
    primitive = {"string": str, "integer": int, "number": float, "boolean": bool}
    if json_type in primitive:
        enum = schema.get("enum")
        if isinstance(enum, list) and enum:
            return Literal[tuple(enum)]  # type: ignore[valid-type]
        return primitive[json_type]
    if json_type == "array":
        items = schema.get("items", {})
        if not isinstance(items, dict):
            raise ValueError(f"工具 '{tool_name}' 的数组 items 必须是 object")
        item_type = _python_type_for_schema(items, definitions=definitions, tool_name=tool_name, model_name=f"{_identifier(model_name)}Item")
        return list[item_type]
    if json_type == "object":
        if not schema.get("properties"):
            return dict[str, Any]
        return _model_for_object(schema, model_name, definitions, tool_name)
    raise ValueError(f"工具 '{tool_name}' 使用了不支持的 JSON Schema 类型: {json_type!r}")


def _resolve_schema(schema: dict[str, Any], definitions: dict[str, Any], tool_name: str) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    reference = schema["$ref"]
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        raise ValueError(f"工具 '{tool_name}' 使用了不支持的 schema 引用: {reference!r}")
    key = reference.removeprefix("#/$defs/")
    resolved = definitions.get(key)
    if not isinstance(resolved, dict):
        raise ValueError(f"工具 '{tool_name}' 引用了未定义 schema: {key}")
    return resolved


def _allows_null(schema: dict[str, Any]) -> bool:
    variants = schema.get("anyOf", schema.get("oneOf"))
    return isinstance(variants, list) and any(
        isinstance(item, dict) and item.get("type") == "null" for item in variants
    )


def _raise_for_contract_failure(result: str) -> None:
    """Make a machine-readable validation response non-terminal for CrewAI."""

    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return
    if isinstance(payload, dict) and payload.get("ok") is False:
        # Preserve the complete machine-readable envelope (error_type, paths,
        # codes and messages) in the exception text that CrewAI feeds back to
        # the Agent for a bounded correction turn.
        detail = json.dumps(payload, ensure_ascii=False)
        raise ValueError(f"Project Contract 校验失败: {detail}")


def _ensure_allowed_keys(
    schema: dict[str, Any], allowed: set[str], tool_name: str, location: str
) -> None:
    unsupported = set(schema) - allowed
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"工具 '{tool_name}' 的 {location} 包含不支持的约束: {names}")


def _identifier(name: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in name) or "Tool"

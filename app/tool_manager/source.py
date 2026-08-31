from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol
import json
import re

from app.execution_context import ExecutionContext


# ── ToolDef ────────────────────────────────────


@dataclass
class ToolDef:
    """工具定义 —— 纯数据，描述工具长什么样。

    不携带执行能力。执行逻辑在 ToolSource.execute() 中，
    这样本地函数和 MCP 远端工具共用同一数据结构。
    """

    name: str
    description: str
    parameters: dict
    execution_modes: tuple[str, ...] | None = None
    # ``final`` means a successful invocation is the node's terminal answer.
    # ``continue`` is used for iterative tools such as file writes and reads.
    completion_policy: str = "continue"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(f"工具名称必须是合法标识符: {self.name!r}")
        if not isinstance(self.description, str):
            raise ValueError(f"工具 '{self.name}' 的 description 必须是字符串")
        if not isinstance(self.parameters, dict):
            raise ValueError(f"工具 '{self.name}' 的 parameters 必须是 object")
        schema_type = self.parameters.get("type", "object")
        if schema_type != "object":
            raise ValueError(f"工具 '{self.name}' 的 parameters 根类型必须是 object")
        properties = self.parameters.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"工具 '{self.name}' 的 properties 必须是 object")
        required = self.parameters.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ValueError(f"工具 '{self.name}' 的 required 必须是字符串数组")
        unknown_required = set(required) - set(properties)
        if unknown_required:
            raise ValueError(
                f"工具 '{self.name}' 的 required 引用了未知字段: {', '.join(sorted(unknown_required))}"
            )
        if self.execution_modes is not None:
            allowed_modes = {"exclusive", "partitioned", "integration", "quality_gate"}
            if not self.execution_modes or any(mode not in allowed_modes for mode in self.execution_modes):
                raise ValueError(f"工具 '{self.name}' 的 execution_modes 包含未知执行模式")
        if self.completion_policy not in {"continue", "final"}:
            raise ValueError(
                f"工具 '{self.name}' 的 completion_policy 必须是 'continue' 或 'final'"
            )


class ToolResultStatus(str, Enum):
    """工具调用的控制面状态；与 Agent/Node 状态保持正交。"""

    COMPLETED = "completed"
    FAILED = "failed"
    RETRYABLE = "retryable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolResult:
    """统一工具结果信封。

    领域服务仍可返回字符串；ToolSource 在边界将其解释为该信封。
    ``ok=false`` 的领域响应不会被当作终态成功。
    """

    tool_name: str
    status: ToolResultStatus
    message: str = ""
    data: Any = None
    error_type: str | None = None
    retryable: bool = False
    expected_tool: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ToolResultStatus.COMPLETED

    @classmethod
    def from_value(cls, tool_name: str, value: Any) -> "ToolResult":
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, dict) and "ok" in value and not isinstance(value.get("ok"), bool):
            return cls(
                tool_name=tool_name,
                status=ToolResultStatus.FAILED,
                message="工具结果 ok 字段必须是 boolean",
                data=value,
                error_type="tool_result_invalid",
            )
        if isinstance(value, dict) and value.get("ok") is False:
            detail = value.get("errors") or value.get("details")
            message = str(value.get("message") or value.get("error") or "工具返回失败")
            if detail:
                message = f"{message}: {json.dumps(detail, ensure_ascii=False)}"
            raw_status = value.get("status")
            status = (
                ToolResultStatus.BLOCKED
                if raw_status == ToolResultStatus.BLOCKED.value
                else ToolResultStatus.RETRYABLE
                if value.get("retryable") or raw_status == ToolResultStatus.RETRYABLE.value
                else ToolResultStatus.FAILED
            )
            return cls(
                tool_name=tool_name,
                status=status,
                message=message,
                data=value,
                error_type=str(value.get("error_type") or "tool_result_invalid"),
                retryable=bool(value.get("retryable")) or status is ToolResultStatus.RETRYABLE,
                expected_tool=(str(value["expected_tool"]) if value.get("expected_tool") else None),
            )
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                return cls.from_value(tool_name, parsed)
        return cls(tool_name=tool_name, status=ToolResultStatus.COMPLETED, message=str(value), data=value)

    @classmethod
    def failure(
        cls, tool_name: str, error: BaseException, *, error_type: str | None = None
    ) -> "ToolResult":
        kind = error_type or classify_tool_error(error)
        retryable = kind in {"tool_validation", "input_missing", "tool_transport", "tool_protocol"}
        return cls(
            tool_name=tool_name,
            status=ToolResultStatus.RETRYABLE if retryable else ToolResultStatus.FAILED,
            message=str(error),
            error_type=kind,
            retryable=retryable,
        )


class ToolExecutionError(RuntimeError, ValueError):
    """工具执行失败，明确区别于动态能力缺失。"""

    def __init__(self, result: ToolResult, *, cause: BaseException | None = None) -> None:
        self.result = result
        self.cause = cause
        status = getattr(result.status, "value", str(result.status))
        super().__init__(
            f"工具 '{result.tool_name}' 执行失败 [{result.error_type or status}]: {result.message}"
        )


class ToolDiscoveryError(RuntimeError):
    """动态来源声明不可用；与工具执行和能力审批保持不同语义。"""

    def __init__(self, source_name: str, cause: BaseException) -> None:
        self.source_name = source_name
        self.cause = cause
        super().__init__(f"工具来源 '{source_name}' 发现失败: {cause}")


def classify_tool_error(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "tool_authorization"
    if isinstance(error, (FileNotFoundError, LookupError)):
        return "input_missing"
    if isinstance(error, (TypeError, ValueError)):
        return "tool_validation"
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return "tool_transport"
    return "tool_execution"


class ToolExposure(str, Enum):
    """工具向普通 Agent 暴露时的默认策略。"""

    ALWAYS = "always"
    ON_DEMAND = "on_demand"
    CONFIRM = "confirm"
    DENIED = "denied"


# ── ToolSource 基类 ───────────────────────────


class ToolSource(ABC):
    """工具来源基类。

    每种来源实现自己的 discover() + execute()：
      - discover() → 返回纯 ToolDef 列表
      - execute()  → 用自己的方式执行工具

    本地函数、MCP 远端、命令行沙箱 —— 差别只在 execute() 里。
    """

    is_dynamic = False

    @abstractmethod
    def discover(self) -> list[ToolDef]:
        """发现该来源提供的工具列表。"""
        ...

    @abstractmethod
    def execute(
        self,
        name: str,
        arguments: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> str:
        """执行工具并返回结果。

        Args:
            name: 工具名称
            arguments: 已解析的参数字典（JSON → dict）
        """
        ...

    def execute_safe(
        self,
        name: str,
        arguments: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> ToolResult:
        """统一调用边界；动态能力请求不得由工具异常隐式产生。"""
        try:
            return ToolResult.from_value(name, self.execute(name, arguments, context=context))
        except ToolExecutionError as error:
            return error.result
        except Exception as error:
            return ToolResult.failure(name, error)


# ── 来源实现 ─────────────────────────────────


class ToolSetSource(ToolSource):
    """本地 ToolSet 工具来源。

    本地工具不做动态扫描。开发者以 ToolSet 为单位显式提供工具列表，
    ToolGateway 将其包装为 CrewAI 工具并暴露给对应 domain 的 Agent。

    使用示例::

        ToolSetSource([
            (ToolDef(name="save", description=..., parameters=...), save_fn),
            (ToolDef(name="load", description=..., parameters=...), load_fn),
        ])
    """

    def __init__(self, tools: list[tuple[ToolDef, Callable]]) -> None:
        self._defs: list[ToolDef] = []
        self._fns: dict[str, Callable] = {}
        for tool_def, fn in tools:
            if tool_def.name in self._fns:
                raise ValueError(f"ToolSet 中存在重复工具名: '{tool_def.name}'")
            self._defs.append(tool_def)
            self._fns[tool_def.name] = fn

    def discover(self) -> list[ToolDef]:
        return self._defs

    def execute(
        self,
        name: str,
        arguments: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> str:
        try:
            fn = self._fns[name]
            result = str(fn(**arguments))
        except Exception as error:
            failure = ToolResult.failure(name, error)
            _record_memory_tool_result(context, name, str(failure.message), tool_result=failure)
            raise ToolExecutionError(failure, cause=error) from error
        parsed = ToolResult.from_value(name, result)
        _record_memory_tool_result(context, name, result, tool_result=parsed)
        return result


class ExecutionToolSetSource(ToolSource):
    """需要编排层可信上下文的本地工具来源。

    与 ``ToolSetSource`` 相比，函数第一个参数固定为 ``ExecutionContext``。
    ``ProjectOSTool`` 会在运行时注入它；LLM 只看到 ToolDef 中声明的业务参数，
    因而无法指定 trace、WorkItem 或 Agent 身份。
    """

    def __init__(self, tools: list[tuple[ToolDef, Callable]]) -> None:
        self._defs: list[ToolDef] = []
        self._fns: dict[str, Callable] = {}
        for tool_def, fn in tools:
            if tool_def.name in self._fns:
                raise ValueError(f"ToolSet 中存在重复工具名: '{tool_def.name}'")
            self._defs.append(tool_def)
            self._fns[tool_def.name] = fn

    def discover(self) -> list[ToolDef]:
        return self._defs

    def execute(
        self,
        name: str,
        arguments: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> str:
        if context is None:
            error = RuntimeError(f"工具 '{name}' 必须由 GraphRunner 在 Trace 中执行")
            failure = ToolResult.failure(name, error)
            raise ToolExecutionError(failure, cause=error) from error
        try:
            fn = self._fns[name]
            result = str(fn(context, **arguments))
        except Exception as error:
            failure = ToolResult.failure(name, error)
            _record_memory_tool_result(context, name, str(error), tool_result=failure)
            raise ToolExecutionError(failure, cause=error) from error
        parsed = ToolResult.from_value(name, result)
        _record_memory_tool_result(context, name, result, tool_result=parsed)
        return result


def _record_memory_tool_result(
    context: ExecutionContext | None,
    tool_name: str,
    content: str,
    *,
    tool_result: ToolResult | None = None,
) -> None:
    if context is None or context.memory is None:
        return
    context.memory.append(
        trace_id=context.trace_id,
        role="tool",
        event_type="tool_result",
        content=content,
        work_item_id=context.work_item_id,
        agent_id=context.agent_id,
        tool_name=tool_name,
        metadata={
            "execution_mode": context.execution_mode.value,
            "ok": tool_result.ok if tool_result is not None else True,
            "status": tool_result.status.value if tool_result is not None else ToolResultStatus.COMPLETED.value,
            "error_type": tool_result.error_type if tool_result is not None else None,
            "retryable": tool_result.retryable if tool_result is not None else False,
        },
    )


class MCPClient(Protocol):
    """MCP 传输适配契约，避免工具层绑定某个 MCP SDK。"""

    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        ...


class MCPToolSource(ToolSource):
    """外部动态发现的工具来源。

    通过 MCP 等协议从远端服务获取工具列表。
    用于本地工具也无法满足需求时的最后兜底。

    MCP 是动态来源：Catalog 在查询时刷新其声明，执行时仍由同一 source
    调用远端。因此工具发现和工具调用共享同一个适配器。

    使用示例（将来）::

        MCPToolSource(client=MCPConnector("http://code-mcp:8080"))
    """

    is_dynamic = True

    def __init__(
        self,
        client: MCPClient | None = None,
        *,
        connector: MCPClient | None = None,
    ) -> None:
        if client is not None and connector is not None:
            raise ValueError("client 和 connector 不能同时提供")
        self._client = client or connector

    def discover(self) -> list[ToolDef]:
        if self._client is None:
            return []
        return [self._to_tool_def(tool) for tool in self._client.list_tools()]

    def execute(
        self,
        name: str,
        arguments: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> str:
        if self._client is None:
            error = RuntimeError("MCP client 尚未配置")
            failure = ToolResult.failure(name, error, error_type="tool_transport")
            raise ToolExecutionError(failure, cause=error) from error
        try:
            result = str(self._client.call_tool(name, arguments))
        except Exception as error:
            failure = ToolResult.failure(name, error, error_type="tool_transport")
            _record_memory_tool_result(context, name, str(error), tool_result=failure)
            raise ToolExecutionError(failure, cause=error) from error
        parsed = ToolResult.from_value(name, result)
        _record_memory_tool_result(context, name, result, tool_result=parsed)
        return result

    @staticmethod
    def _to_tool_def(tool: dict[str, Any]) -> ToolDef:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("MCP 工具声明缺少合法 name")
        schema = tool.get("inputSchema", tool.get("parameters", {"type": "object", "properties": {}}))
        if not isinstance(schema, dict):
            raise ValueError(f"MCP 工具 '{tool['name']}' 的 inputSchema 必须是 object")
        return ToolDef(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=schema,
        )

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol


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
    def execute(self, name: str, arguments: dict) -> str:
        """执行工具并返回结果。

        Args:
            name: 工具名称
            arguments: 已解析的参数字典（JSON → dict）
        """
        ...


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

    def execute(self, name: str, arguments: dict) -> str:
        fn = self._fns[name]
        return str(fn(**arguments))


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

    def execute(self, name: str, arguments: dict) -> str:
        if self._client is None:
            raise RuntimeError("MCP client 尚未配置")
        return str(self._client.call_tool(name, arguments))

    @staticmethod
    def _to_tool_def(tool: dict[str, Any]) -> ToolDef:
        return ToolDef(
            name=tool["name"],
            description=tool.get("description", ""),
            parameters=tool.get("inputSchema", tool.get("parameters", {})),
        )


class ExternalDynamicSource(MCPToolSource):
    """已废弃的 MCPToolSource 兼容别名。"""

    def __init__(self, connector: MCPClient | None = None) -> None:
        super().__init__(connector=connector)

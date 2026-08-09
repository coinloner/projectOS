from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


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


# ── ToolSource 基类 ───────────────────────────


class ToolSource(ABC):
    """工具来源基类。

    每种来源实现自己的 discover() + execute()：
      - discover() → 返回纯 ToolDef 列表
      - execute()  → 用自己的方式执行工具

    本地函数、MCP 远端、命令行沙箱 —— 差别只在 execute() 里。
    """

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
    ToolManager 将其暴露给对应 domain 的 Agent。

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
            self._defs.append(tool_def)
            self._fns[tool_def.name] = fn

    def discover(self) -> list[ToolDef]:
        return self._defs

    def execute(self, name: str, arguments: dict) -> str:
        fn = self._fns[name]
        return str(fn(**arguments))

class ExternalDynamicSource(ToolSource):
    """外部动态发现的工具来源。

    通过 MCP 等协议从远端服务获取工具列表。
    用于本地工具也无法满足需求时的最后兜底。

    External tier 的工具不持久化 —— 每次 list_tools()
    都重新 discover()，用完即弃。

    使用示例（将来）::

        ExternalDynamicSource(connector=MCPConnector("http://code-mcp:8080"))
    """

    def __init__(self, connector: object = None) -> None:
        self._connector = connector  # 将来是 MCPConnector 或其他发现机制

    def discover(self) -> list[ToolDef]:
        # TODO: 通过 MCP 协议向远端请求工具列表
        return []

    def execute(self, name: str, arguments: dict) -> str:
        # TODO: 委托给 MCP connector 执行
        return f"Error: MCP 工具 '{name}' 尚未实现"

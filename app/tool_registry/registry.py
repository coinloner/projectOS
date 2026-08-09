from __future__ import annotations

import json
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.tool_manager.source import ToolSource


class ToolRegistry:
    """工具注册中心 —— 纯存储层。

    ToolManager 发现工具后通过 register() 存入，
    Agent 通过 ToolManager 间接调用 list_tools() / call()。

    不关心工具从哪来、何时加载、暴露给谁 —— 这些由 ToolManager 负责。
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    # ── 注册 ──────────────────────────────────

    def register(
        self,
        name: str,
        tool_def: dict,
        source: ToolSource,
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名称（LLM 可见）
            tool_def: OpenAI 格式的工具定义
            source: 提供该工具的 ToolSource（执行时委托回去）
        """
        self._tools[name] = {
            "def": tool_def,
            "source": source,
        }

    # ── 发现 ──────────────────────────────────

    def list_tools(self) -> Optional[list[dict]]:
        """返回 OpenAI 格式的工具定义列表，供 LLM 使用。"""
        if not self._tools:
            return None
        return [t["def"] for t in self._tools.values()]

    # ── 执行 ──────────────────────────────────

    def call(self, name: str, arguments: str) -> str:
        """执行已注册的工具并返回结果。

        委托给 ToolSource.execute() —— 本地函数和 MCP 远端
        的执行路径在这里分叉。
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: 未知工具 '{name}'"

        try:
            args = (
                json.loads(arguments)
                if isinstance(arguments, str)
                else arguments
            )
            source: ToolSource = tool["source"]
            return source.execute(name, args)
        except Exception as e:
            return f"Error: 工具 '{name}' 执行失败: {e}"

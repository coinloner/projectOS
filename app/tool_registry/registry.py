import json
from typing import Any, Callable, Optional


class ToolRegistry:
    """工具注册中心 —— Agent 通过它发现和调用工具。

    当前阶段：人工注册（方案 A：启动时集中注册）
    将来阶段：MCP 动态发现（内部实现切换，接口不变）
    """

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Any]] = {}

    # ── 注册 ──────────────────────────────────

    def register(
        self,
        name: str,
        fn: Callable,
        description: str,
        parameters: dict,
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名称（LLM 可见）
            fn: 工具实现函数
            description: 工具用途（LLM 据此判断何时调用）
            parameters: JSON Schema 格式的参数定义
        """
        self._tools[name] = {
            "fn": fn,
            "def": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }

    # ── 发现 ──────────────────────────────────

    def list_tools(self) -> Optional[list[dict]]:
        """返回 OpenAI 格式的工具定义列表，供 LLM 使用。

        Agent 通过此方法询问："有什么工具可用？"
        """
        if not self._tools:
            return None
        return [t["def"] for t in self._tools.values()]

    # ── 执行 ──────────────────────────────────

    def call(self, name: str, arguments: str) -> str:
        """执行已注册的工具并返回结果。

        Args:
            name: 工具名称
            arguments: JSON 格式的参数

        Returns:
            工具执行结果字符串
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
            result = tool["fn"](**args)
            return str(result)
        except Exception as e:
            return f"Error: 工具 '{name}' 执行失败: {e}"

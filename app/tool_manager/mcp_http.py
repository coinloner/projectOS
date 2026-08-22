"""MCP Streamable HTTP 传输适配：把 mcp SDK 会话包装成同步 MCPClient。

MCPToolSource 只依赖 MCPClient 协议（list_tools/call_tool）；本模块提供
它的真实 HTTP 实现，供注册到 Gateway 的动态来源使用。每次调用建立独立
会话，避免长时间持连接。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.tool_manager.source import MCPClient


class StreamableHttpMCPClient(MCPClient):
    """通过 MCP Streamable HTTP 协议发现并调用远端工具。

    每次调用建立独立会话。terminate_on_close=False 表示不等待服务端回收
    会话，让工具调用尽快返回；会话残留由服务端按 TTL 清理。
    """

    def __init__(self, url: str) -> None:
        self._url = url

    def list_tools(self) -> list[dict[str, Any]]:
        return asyncio.run(self._list_tools())

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return asyncio.run(self._call_tool(name, arguments))

    async def _list_tools(self) -> list[dict[str, Any]]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(
            self._url, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "inputSchema": tool.inputSchema or {},
                    }
                    for tool in result.tools
                ]

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(
            self._url, terminate_on_close=False
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                # 优先返回文本块，Agent 直接可读；没有文本块时退回结构化内容。
                blocks = [
                    block.text
                    for block in result.content
                    if getattr(block, "text", None)
                ]
                if blocks:
                    return "\n".join(blocks)
                if result.structuredContent:
                    return result.structuredContent
                return json.dumps(
                    [block.model_dump() for block in result.content],
                    ensure_ascii=False,
                    default=str,
                )

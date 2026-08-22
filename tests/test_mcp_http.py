"""MCP Streamable HTTP 传输与演示文档服务的真实往返测试。"""

import socket
import subprocess
import sys
import time
import unittest

from app.tool_manager.mcp_http import StreamableHttpMCPClient
from app.tool_manager.source import MCPToolSource

SERVER_PORT = 8099
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}/mcp"


class McpHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.demo.docs_mcp_server",
                "--port",
                str(SERVER_PORT),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if cls._server.poll() is not None:
                raise RuntimeError("demo MCP server 启动失败")
            try:
                with socket.create_connection(("127.0.0.1", SERVER_PORT), timeout=1):
                    break
            except OSError:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.terminate()
        try:
            cls._server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls._server.kill()

    def test_discovers_and_calls_tools_over_streamable_http(self) -> None:
        source = MCPToolSource(client=StreamableHttpMCPClient(SERVER_URL))

        tools = source.discover()

        names = {tool.name for tool in tools}
        self.assertEqual(names, {"search_docs", "get_doc"})
        search = next(tool for tool in tools if tool.name == "search_docs")
        self.assertIn("query", search.parameters.get("properties", {}))

        result = source.execute("search_docs", {"query": "idempotency"})
        self.assertIn("幂等键", result)

        full = source.execute("get_doc", {"topic": "conflict"})
        self.assertIn("409", full)
        self.assertIn("[conflict]", full)

    def test_unknown_topic_returns_friendly_hint(self) -> None:
        source = MCPToolSource(client=StreamableHttpMCPClient(SERVER_URL))
        result = source.execute("get_doc", {"topic": "不存在的主题"})
        self.assertIn("未知主题", result)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource, ToolDef, ToolSetSource


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.discoveries = 0

    def list_tools(self) -> list[dict]:
        self.discoveries += 1
        return [
            {
                "name": "search_external_docs",
                "description": "Search external documentation",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return f"found: {arguments['query']}"


class ToolGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = ToolGateway()
        self.gateway.register_toolset(
            "requirement",
            "local",
            toolset=ToolSetSource(
                [
                    (
                        ToolDef(
                            name="save_requirement",
                            description="Save the requirement",
                            parameters={"type": "object", "properties": {}},
                        ),
                        lambda: "saved",
                    )
                ]
            ),
        )

    def test_local_tool_is_exposed_as_crewai_tool_and_executable(self) -> None:
        tools = self.gateway.tools_for("requirement")

        self.assertEqual([tool.name for tool in tools], ["save_requirement"])
        self.assertEqual(tools[0].run(), "saved")

    def test_mcp_source_requires_explicit_activation(self) -> None:
        client = FakeMCPClient()
        self.gateway.register_source(
            "requirement", "docs-mcp", MCPToolSource(client)
        )

        self.assertEqual(
            [tool.name for tool in self.gateway.tools_for("requirement")],
            ["save_requirement"],
        )

        self.gateway.activate_source("requirement", "docs-mcp")
        tools = self.gateway.tools_for("requirement")

        self.assertEqual(
            [tool.name for tool in tools],
            ["save_requirement", "search_external_docs"],
        )
        self.assertEqual(tools[1].run(query="policy"), "found: policy")
        self.assertEqual(client.calls, [("search_external_docs", {"query": "policy"})])

    def test_deactivating_mcp_removes_crewai_tool_exposure(self) -> None:
        client = FakeMCPClient()
        self.gateway.register_source(
            "requirement", "docs-mcp", MCPToolSource(client)
        )
        self.gateway.activate_source("requirement", "docs-mcp")
        active_tools = self.gateway.tools_for("requirement")
        self.gateway.deactivate_source("requirement", "docs-mcp")

        self.assertEqual(
            [tool.name for tool in self.gateway.tools_for("requirement")],
            ["save_requirement"],
        )
        with self.assertRaisesRegex(PermissionError, "未获授权"):
            active_tools[1].run(query="policy")
        self.assertEqual(client.calls, [])

    def test_activating_unknown_source_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在来源"):
            self.gateway.activate_source("requirement", "missing")

    def test_capability_lookup_finds_mcp_without_discovery_or_activation(self) -> None:
        client = FakeMCPClient()
        self.gateway.register_source(
            "requirement",
            "docs-mcp",
            MCPToolSource(client),
            capability="external_research",
        )

        sources = self.gateway.find_sources_for_capability(
            "requirement", "external_research"
        )

        self.assertEqual([source.source_name for source in sources], ["docs-mcp"])
        self.assertEqual(client.discoveries, 0)
        self.assertEqual(
            [tool.name for tool in self.gateway.tools_for("requirement")],
            ["save_requirement"],
        )

    def test_crewai_adapter_validates_arguments_before_source_execution(self) -> None:
        calls: list[str] = []
        self.gateway.register_toolset(
            "research",
            "local",
            toolset=ToolSetSource(
                [
                    (
                        ToolDef(
                            name="search",
                            description="Search documents",
                            parameters={
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                                "additionalProperties": False,
                            },
                        ),
                        lambda query: calls.append(query) or query,
                    )
                ]
            ),
        )
        tool = self.gateway.tools_for("research")[0]

        with self.assertRaisesRegex(ValueError, "arguments validation failed"):
            tool.run()
        with self.assertRaisesRegex(ValueError, "extra_forbidden"):
            tool.run(query="policy", extra="unexpected")

        self.assertEqual(tool.run(query="policy"), "policy")
        self.assertEqual(calls, ["policy"])




if __name__ == "__main__":
    unittest.main()

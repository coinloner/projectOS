import unittest

from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource, ToolDef, ToolSetSource
from app.tool_manager.grants import CapabilityGrant
from app.execution_context import ExecutionContext
from app.tool_manager.crewai_adapter import args_schema_for
from crewai.utilities.agent_utils import convert_tools_to_openai_schema


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

    def test_completion_policy_maps_to_crewai_terminal_answer(self) -> None:
        self.gateway.register_toolset(
            "artifacts",
            "local",
            toolset=ToolSetSource(
                [
                    (
                        ToolDef(
                            name="save_artifact",
                            description="Save an artifact",
                            parameters={"type": "object", "properties": {}},
                            completion_policy="final",
                        ),
                        lambda: "saved",
                    ),
                    (
                        ToolDef(
                            name="write_file",
                            description="Write one file",
                            parameters={"type": "object", "properties": {}},
                        ),
                        lambda: "written",
                    ),
                ]
            ),
        )

        tools = {tool.name: tool for tool in self.gateway.tools_for("artifacts")}
        self.assertTrue(tools["save_artifact"].result_as_answer)
        self.assertFalse(tools["write_file"].result_as_answer)

    def test_completion_policy_rejects_unknown_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "completion_policy"):
            ToolDef(
                name="broken",
                description="broken",
                parameters={"type": "object", "properties": {}},
                completion_policy="stop",
            )

    def test_mcp_source_requires_explicit_activation(self) -> None:
        client = FakeMCPClient()
        self.gateway.register_source(
            "requirement", "docs-mcp", MCPToolSource(client)
        )

        self.assertEqual(
            [tool.name for tool in self.gateway.tools_for("requirement")],
            ["save_requirement"],
        )

        self.gateway.activate_grant(CapabilityGrant(
            grant_id="grant-requirement", trace_id="trace-test", work_item_id="wi-test",
            capability="requirement", source_name="docs-mcp", scope="trace",
        ))
        context = ExecutionContext(trace_id="trace-test", work_item_id="wi-test", agent_id="agent")
        tools = self.gateway.tools_for("requirement", context=context)

        self.assertEqual(
            [tool.name for tool in tools],
            ["save_requirement", "search_external_docs"],
        )
        self.assertEqual(tools[1].run(query="policy"), "found: policy")
        self.assertEqual(client.calls, [("search_external_docs", {"query": "policy"})])

    def test_grant_for_capability_covers_registered_domains_without_widening_scope(self) -> None:
        for domain in ("requirement", "architecture", "code", "review"):
            self.gateway.register_source(
                domain,
                "docs-mcp",
                MCPToolSource(FakeMCPClient()),
                capability="external_documentation",
            )

        self.gateway.activate_grant(CapabilityGrant(
            grant_id="grant-trace", trace_id="trace-test", work_item_id="wi-test",
            capability="external_documentation", source_name="docs-mcp", scope="trace",
        ))
        context = ExecutionContext(trace_id="trace-test", work_item_id="wi-test", agent_id="agent")
        for domain in ("requirement", "architecture", "code", "review"):
            names = [tool.name for tool in self.gateway.tools_for(domain, context=context)]
            self.assertIn("search_external_docs", names, domain)
        # 无关 domain 不受影响
        self.assertNotIn(
            "search_external_docs",
            [tool.name for tool in self.gateway.tools_for("test")],
        )

    def test_deactivating_mcp_removes_crewai_tool_exposure(self) -> None:
        client = FakeMCPClient()
        self.gateway.register_source(
            "requirement", "docs-mcp", MCPToolSource(client)
        )
        grant = CapabilityGrant(
            grant_id="grant-revoke", trace_id="trace-test", work_item_id="wi-test",
            capability="requirement", source_name="docs-mcp", scope="trace",
        )
        context = ExecutionContext(trace_id="trace-test", work_item_id="wi-test", agent_id="agent")
        self.gateway.activate_grant(grant)
        active_tools = self.gateway.tools_for("requirement", context=context)
        self.gateway.deactivate_grant(grant.grant_id)

        self.assertEqual(
            [tool.name for tool in self.gateway.tools_for("requirement", context=context)],
            ["save_requirement"],
        )
        with self.assertRaisesRegex(PermissionError, "未获授权"):
            active_tools[1].run(query="policy")
        self.assertEqual(client.calls, [])

    def test_activating_unknown_source_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在来源"):
            self.gateway.activate_grant(CapabilityGrant(
                grant_id="grant-missing", trace_id="trace-test", work_item_id="wi-test",
                capability="missing", source_name="missing", scope="trace",
            ))

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

    def test_scoped_grants_limit_dynamic_tools_to_node_trace_or_project(self) -> None:
        for domain in ("architecture", "code"):
            self.gateway.register_source(
                domain,
                "docs-mcp",
                MCPToolSource(FakeMCPClient()),
                capability="external_documentation",
            )
        node = ExecutionContext(trace_id="trace-a", work_item_id="node-a", agent_id="agent")
        sibling = ExecutionContext(trace_id="trace-a", work_item_id="node-b", agent_id="agent")
        other_trace = ExecutionContext(trace_id="trace-b", work_item_id="node-c", agent_id="agent")

        grant = CapabilityGrant(
            grant_id="grant-node", trace_id="trace-a", work_item_id="node-a",
            capability="external_documentation", source_name="docs-mcp", scope="node",
        )
        self.gateway.activate_grant(grant)
        self.assertIn("search_external_docs", {t.name for t in self.gateway.tools_for("architecture", context=node)})
        self.assertNotIn("search_external_docs", {t.name for t in self.gateway.tools_for("architecture", context=sibling)})
        self.assertNotIn("search_external_docs", {t.name for t in self.gateway.tools_for("architecture", context=other_trace)})
        self.assertNotIn("search_external_docs", {t.name for t in self.gateway.tools_for("architecture")})

        trace_grant = CapabilityGrant(
            grant_id="grant-trace", trace_id="trace-a", work_item_id="node-a",
            capability="external_documentation", source_name="docs-mcp", scope="trace",
        )
        self.gateway.activate_grant(trace_grant)
        self.assertIn("search_external_docs", {t.name for t in self.gateway.tools_for("code", context=sibling)})
        self.assertNotIn("search_external_docs", {t.name for t in self.gateway.tools_for("code", context=other_trace)})

        project_grant = CapabilityGrant(
            grant_id="grant-project", trace_id="trace-a", work_item_id="node-a",
            capability="external_documentation", source_name="docs-mcp", scope="project",
        )
        self.gateway.activate_grant(project_grant)
        self.assertIn("search_external_docs", {t.name for t in self.gateway.tools_for("code", context=other_trace)})

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

    def test_optional_fields_are_nullable_in_strict_openai_schema(self) -> None:
        """Optional ToolDef values must not become ``string + default=null``.

        CrewAI sends every tool as ``strict: true``.  Its serializer makes all
        properties required on the wire, so optional values need an explicit
        ``null`` branch while direct ``tool.run`` calls should still be able
        to omit them and use the Python default.
        """
        definition = ToolDef(
            name="configure",
            description="Configure an optional value",
            parameters={
                "type": "object",
                "properties": {
                    "profile": {"type": "string"},
                    "application": {"type": "string", "default": None},
                },
                "required": ["profile"],
            },
        )
        schema = args_schema_for(definition)
        self.assertNotIn("application", schema.model_json_schema()["required"])

        tool = self.gateway.tools_for("requirement")[0]
        tool.args_schema = schema
        wire, _, _ = convert_tools_to_openai_schema([tool])
        parameters = wire[0]["function"]["parameters"]
        self.assertIn("application", parameters["required"])
        self.assertEqual(
            parameters["properties"]["application"]["anyOf"],
            [{"type": "string"}, {"type": "null"}],
        )

    def test_tool_wire_schema_is_valid_strict_function_definition(self) -> None:
        """A one-field ProjectOS tool is a valid strict function definition."""
        contract = self.gateway.tools_for("requirement")[0]
        wire, _, _ = convert_tools_to_openai_schema([contract])
        function = wire[0]["function"]
        self.assertTrue(wire[0]["type"] == "function")
        self.assertTrue(function["name"])
        self.assertEqual(function["parameters"]["type"], "object")
        self.assertFalse(function["parameters"]["additionalProperties"])




if __name__ == "__main__":
    unittest.main()

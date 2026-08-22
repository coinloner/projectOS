import tempfile
import unittest

from app.domain import (
    register_architecture_tools,
    register_bootstrap_tools,
    register_code_tools,
    register_requirement_tools,
    register_review_tools,
    register_task_tools,
    register_test_tools,
)
from app.tool_manager.gateway import ToolGateway
from app.execution_context import ExecutionContext, ExecutionMode


class DomainToolContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.gateway = ToolGateway()
        for register in (
            register_requirement_tools,
            register_architecture_tools,
            register_task_tools,
            register_bootstrap_tools,
            register_code_tools,
            register_test_tools,
            register_review_tools,
        ):
            register(self.gateway, self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_each_domain_exposes_its_explicit_tool_contract(self) -> None:
        expected = {
            "requirement": {"save_requirement", "load_requirement"},
            "architecture": {
                "load_artifact", "save_architecture", "load_architecture_input",
                "write_staged_architecture", "create_architecture_candidate",
            },
            "task": {"load_task_input", "write_staged_tasks", "create_tasks_candidate"},
            "bootstrap": {"configure_runtime", "inspect_runtime", "prepare_environment", "load_artifact", "save_environment"},
            "code": {
                "load_artifact",
                "save_implementation",
                "list_workspace_files",
                "read_workspace_file",
                "write_workspace_file",
                "inspect_runtime",
                "load_code_input",
                "write_staged_code_file",
            },
            "test": {
                "load_artifact",
                "save_tests",
                "list_workspace_files",
                "read_workspace_file",
                "write_test_file",
                "run_sandbox_check",
            },
            "review": {
                "load_artifact",
                "save_review",
                "list_workspace_files",
                "read_workspace_file",
                "inspect_runtime",
                "inspect_quality",
                "list_sandbox_evidence",
                "load_sandbox_evidence",
            },
        }

        for domain, names in expected.items():
            self.assertEqual(
                {tool.name for tool in self.gateway.tools_for(domain)},
                names,
                domain,
            )

    def test_only_code_domain_can_write_general_workspace_files(self) -> None:
        code_tools = {tool.name for tool in self.gateway.tools_for("code")}

        self.assertIn("write_workspace_file", code_tools)
        for domain in ("requirement", "architecture", "task", "bootstrap", "test", "review"):
            self.assertNotIn(
                "write_workspace_file",
                {tool.name for tool in self.gateway.tools_for(domain)},
                domain,
            )

    def test_code_partition_sees_only_isolated_staging_tools(self) -> None:
        context = ExecutionContext(
            trace_id="tr-code", work_item_id="code-backend",
            agent_id="code_agent", execution_mode=ExecutionMode.PARTITIONED,
            output_slot="backend",
        )
        self.assertEqual(
            {tool.name for tool in self.gateway.tools_for("code", context=context)},
            {"load_code_input", "write_staged_code_file"},
        )

    def test_only_test_domain_can_execute_or_write_tests(self) -> None:
        test_tools = {tool.name for tool in self.gateway.tools_for("test")}

        self.assertTrue({"write_test_file", "run_sandbox_check"} <= test_tools)
        for domain in ("requirement", "architecture", "task", "bootstrap", "code", "review"):
            names = {tool.name for tool in self.gateway.tools_for(domain)}
            self.assertNotIn("write_test_file", names, domain)
            self.assertNotIn("run_sandbox_check", names, domain)

    def test_architecture_artifact_tools_are_visible_only_in_their_execution_mode(self) -> None:
        scope_context = ExecutionContext(
            trace_id="tr-architecture", work_item_id="architecture-api",
            agent_id="architecture_agent", execution_mode=ExecutionMode.PARTITIONED,
            output_slot="api",
        )
        integration_context = ExecutionContext(
            trace_id="tr-architecture", work_item_id="architecture-integration",
            agent_id="architecture_agent", execution_mode=ExecutionMode.INTEGRATION,
            publish_target="architecture",
        )

        self.assertEqual(
            {tool.name for tool in self.gateway.tools_for("architecture", context=scope_context)},
            {"load_architecture_input", "write_staged_architecture"},
        )
        self.assertEqual(
            {tool.name for tool in self.gateway.tools_for("architecture", context=integration_context)},
            {"load_architecture_input", "create_architecture_candidate"},
        )


if __name__ == "__main__":
    unittest.main()

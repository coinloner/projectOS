import tempfile
import unittest

from app.bootstrap.runtime import build_container


class BootstrapRuntimeTest(unittest.TestCase):
    def test_container_installs_all_domains_and_controlled_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            container = build_container(project_path)

            self.assertEqual(
                {definition.id for definition in container.agents.definitions()},
                {
                    "requirement_agent",
                    "architecture_agent",
                    "architecture_contract_agent",
                    "task_agent",
                    "bootstrap_agent",
                    "code_agent",
                    "test_agent",
                    "review_agent",
                    "code_integration_agent",
                },
            )
            self.assertEqual(
                container.agents.definition("architecture_agent").max_parallel_instances,
                4,
            )
            self.assertIsNotNone(container.templates.get("project_delivery"))
            self.assertIsNotNone(container.templates.get("architecture_compact"))
            self.assertIsNotNone(container.templates.get("architecture_parallel"))
            self.assertIsNotNone(container.templates.get("project_delivery_minimal"))
            self.assertEqual(
                {tool.name for tool in container.gateway.tools_for("architecture")},
                {
                    "load_artifact",
                    "save_architecture",
                    "load_architecture_input",
                    "write_staged_architecture",
                    "create_architecture_candidate",
                },
            )
            self.assertEqual(
                {tool.name for tool in container.gateway.tools_for("architecture_contract")},
                {"load_architecture", "load_requirement", "save_implementation_contract"},
            )
            self.assertIsNotNone(container.planner)
            self.assertIsNotNone(container.runner)

    def test_container_compiles_controlled_workflow_without_planner_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            container = build_container(project_path)
            container.artifacts.save("requirement", "# Existing requirement")

            planning = container.planner.plan_controlled_workflow(
                goal="根据需求设计架构",
                plan_id="architecture-api",
                workflow_id="architecture_parallel",
            )

            self.assertEqual(planning.attempts, 0)
            self.assertEqual(planning.plan.template_id, "architecture_parallel")
            self.assertEqual(
                planning.plan.work_items[-1].id,
                "wi-06-architecture-quality-gate",
            )
            memory_events = container.memory.events(planning.plan.trace.trace_id)
            self.assertEqual(
                [event.event_type for event in memory_events[:3]],
                ["goal", "planner_input", "draft_output"],
            )


if __name__ == "__main__":
    unittest.main()

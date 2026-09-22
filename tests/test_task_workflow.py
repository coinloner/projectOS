import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef
from app.artifact.store import ArtifactStore
from app.domain.task.service import TaskArtifactWorkflow
from app.domain.task.tools import register_task_tools
from app.execution_context import ExecutionContext, ExecutionMode
from app.tool_manager.gateway import ToolGateway


class TaskWorkflowTest(unittest.TestCase):
    def test_tools_are_limited_by_standard_execution_mode(self) -> None:
        gateway = ToolGateway()
        with tempfile.TemporaryDirectory() as directory:
            register_task_tools(gateway, directory)

            self.assertEqual(
                {tool.name for tool in gateway.tools_for("task")},
                {"load_task_input", "write_staged_tasks", "create_tasks_candidate"},
            )
            partitioned = ExecutionContext(
                trace_id="trace-1",
                work_item_id="task-plan",
                agent_id="task_agent",
                execution_mode=ExecutionMode.PARTITIONED,
                slot="plan",
            )
            self.assertEqual(
                {tool.name for tool in gateway.tools_for("task", context=partitioned)},
                {"load_task_input", "write_staged_tasks"},
            )

    def test_staged_candidate_is_only_published_by_repository_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ArtifactStore(directory).save("requirement", "# Requirement")
            workflow = TaskArtifactWorkflow(directory)
            input_ref = ArtifactRef.published("requirement")
            partitioned = ExecutionContext(
                trace_id="trace-1",
                work_item_id="task-plan",
                agent_id="task_agent",
                execution_mode=ExecutionMode.PARTITIONED,
                input_refs=(input_ref,),
                slot="plan",
            )
            self.assertEqual(workflow.load_input(partitioned, input_ref.ref_id), "# Requirement")
            workflow.write_staged(partitioned, "# Tasks")

            integration = ExecutionContext(
                trace_id="trace-1",
                work_item_id="task-integration",
                agent_id="task_agent",
                execution_mode=ExecutionMode.INTEGRATION,
                input_refs=(
                    ArtifactRef.staged(
                        artifact_key="tasks",
                        trace_id="trace-1",
                        work_item_id="task-plan",
                        slot="plan",
                    ),
                ),
                publish_target="tasks",
            )
            result = workflow.create_candidate(integration, "# Tasks")
            self.assertIn("ready_for_quality_gate", result)
            self.assertFalse(Path(directory, "tasks.md").exists())


if __name__ == "__main__":
    unittest.main()

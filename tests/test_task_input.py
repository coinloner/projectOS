import unittest

from app.artifact.repository import ArtifactRef
from app.orchestration.node_result import NodeResult
from app.orchestration.plan import ExecutionPlan
from app.orchestration.state import RunState
from app.orchestration.task_input import build_task_input
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.execution_context import ExecutionMode
from app.workflow.compiler import TemplateCompiler
from app.workflow.templates import project_delivery_minimal_template


class TaskInputPackageTest(unittest.TestCase):
    def test_package_contains_scope_contract_without_upstream_content(self) -> None:
        requirement = WorkItem(
            id="requirement",
            agent_id="requirement_agent",
            objective="整理需求",
            output_key="requirement",
        )
        backend = WorkItem(
            id="backend",
            agent_id="code_agent",
            objective="实现 backend Todo API",
            output_key="implementation_backend",
            artifact_key="implementation",
            dependencies=(
                WorkItemDependency("requirement", source=DependencySource.SYSTEM),
            ),
            input_refs=(ArtifactRef.published("architecture"), ArtifactRef.published("environment")),
            execution_mode=ExecutionMode.PARTITIONED,
            output_slot="backend",
            acceptance_criteria=("只实现 Todo API",),
            constraints=("使用 Python 标准库",),
            non_goals=("不修改 frontend",),
        )
        plan = ExecutionPlan(
            id="plan-task-input",
            goal="交付 Todo 项目",
            work_items=(requirement, backend),
            trace=TraceContext(
                requirement_id="req-task-input", trace_id="tr-task-input"
            ),
        )
        state = RunState(plan=plan)
        state.record(
            requirement,
            NodeResult.completed(
                node_id="requirement", agent_id="requirement_agent", content="需求正文不应注入"
            ),
        )

        package = build_task_input(state, backend)
        data = package.as_dict()
        prompt = package.as_prompt()

        self.assertEqual(data["work_item_id"], "backend")
        self.assertEqual(data["scope"]["allowed_paths"], ["workspace/backend/**"])
        self.assertIn("workspace/frontend/**", data["scope"]["forbidden_paths"])
        self.assertEqual(data["dependencies"][0]["status"], "completed")
        self.assertTrue(data["dependencies"][0]["output_available"])
        self.assertIn("architecture", prompt)
        self.assertIn("environment", prompt)
        self.assertNotIn("需求正文不应注入", prompt)
        self.assertIn("不修改 frontend", prompt)

    def test_minimal_code_template_uses_narrow_inputs_and_explicit_constraints(self) -> None:
        trace = TraceContext(
            requirement_id="req-template", trace_id="tr-template"
        )
        plan = TemplateCompiler().compile(
            project_delivery_minimal_template(),
            goal="交付 Todo 项目",
            plan_id="plan-template",
            trace=trace,
            agent_output_keys={
                "task_agent": "tasks",
                "bootstrap_agent": "environment",
                "code_agent": "implementation",
                "code_integration_agent": "implementation_merge",
                "test_agent": "tests",
                "review_agent": "review",
            },
        )
        backend = next(item for item in plan.work_items if item.id.endswith("code-backend"))
        frontend = next(item for item in plan.work_items if item.id.endswith("code-frontend"))

        self.assertEqual(
            [ref.artifact_key for ref in backend.input_refs],
            ["architecture", "environment"],
        )
        self.assertEqual(
            [ref.artifact_key for ref in frontend.input_refs],
            ["architecture", "environment"],
        )
        self.assertTrue(backend.constraints)
        self.assertTrue(backend.non_goals)


if __name__ == "__main__":
    unittest.main()

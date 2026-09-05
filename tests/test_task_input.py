import unittest

from app.artifact.repository import ArtifactRef
from app.orchestration.node_result import NodeResult
from app.orchestration.plan import ExecutionPlan
from app.orchestration.state import RunState
from app.orchestration.task_input import build_task_input
from app.orchestration.trace import TraceContext
from app.orchestration.retry import FailureKind, FailurePackage, FailureSignal
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
            slot="backend",
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
                work_item_id="requirement", agent_id="requirement_agent", content="需求正文不应注入"
            ),
        )

        package = build_task_input(state, backend)
        data = package.as_dict()
        prompt = package.as_prompt()

        self.assertEqual(data["work_item_id"], "backend")
        self.assertEqual(data["goal"], "交付 Todo 项目")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["contract_digest"], backend.contract_digest)
        self.assertEqual(data["output"]["output_kind"], backend.output_kind)
        self.assertNotIn("project_goal", data)
        self.assertNotIn("failure_context", data)
        self.assertEqual(data["scope"]["allowed_paths"], ["workspace/backend/**"])
        self.assertNotIn("forbidden_paths", data["scope"])
        self.assertEqual(data["dependencies"][0]["status"], "completed")
        self.assertTrue(data["dependencies"][0]["output_available"])
        self.assertIn("architecture", prompt)
        self.assertIn("environment", prompt)
        self.assertNotIn("需求正文不应注入", prompt)
        self.assertIn("不修改 frontend", prompt)
        self.assertIn("节点语义契约", prompt)
        self.assertEqual(
            data["semantic_contract"]["fields"]["dependencies"]["consumer"],
            "调度器",
        )

    def test_model_visible_implementation_contract_omits_forbidden_paths(self) -> None:
        item = WorkItem(
            id="code-api",
            agent_id="code_agent",
            objective="实现 API 入口",
            output_key="implementation_api",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
            allowed_paths=("backend/app/**",),
            forbidden_paths=("frontend/**", ".projectos/**"),
            required_paths=("backend/app/main.py",),
            owned_files=("backend/app/main.py",),
            implementation_unit_id="unit-api",
        )
        plan = ExecutionPlan(
            id="plan-model-visible-scope",
            goal="交付 API",
            work_items=(item,),
            trace=TraceContext(requirement_id="req-scope", trace_id="tr-scope"),
        )

        data = build_task_input(RunState(plan=plan), item).as_dict()

        self.assertEqual(data["scope"]["allowed_paths"], ["backend/app/**"])
        self.assertNotIn("forbidden_paths", data["scope"])
        self.assertNotIn("forbidden_paths", data["implementation"])

    def test_code_prompt_makes_write_and_required_path_obligations_explicit(self) -> None:
        item = WorkItem(
            id="code-api",
            agent_id="code_agent",
            objective="实现 API 入口",
            output_key="implementation_api",
            artifact_key="implementation",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
            allowed_paths=("backend/app/main.py",),
            required_paths=("backend/app/main.py",),
        )
        plan = ExecutionPlan(
            id="plan-code-prompt",
            goal="交付 API",
            work_items=(item,),
            trace=TraceContext(requirement_id="req-code-prompt", trace_id="tr-code-prompt"),
        )
        prompt = build_task_input(RunState(plan=plan), item).as_prompt()
        self.assertIn("write_staged_code_file", prompt)
        self.assertIn("backend/app/main.py", prompt)
        self.assertIn("不能调用 save_implementation", prompt)

    def test_semantic_contract_exposes_local_lineage_and_code_field_rules(self) -> None:
        dependency = WorkItem(
            id="architecture", agent_id="architecture_agent", objective="设计", output_key="architecture"
        )
        item = WorkItem(
            id="code-api", agent_id="code_agent", objective="实现 API", output_key="implementation_api",
            dependencies=(WorkItemDependency("architecture", source=DependencySource.SYSTEM),),
            implementation_unit_id="unit-api", execution_mode=ExecutionMode.PARTITIONED,
            slot="backend", owned_files=("backend/app/api.py",), required_paths=("backend/app/api.py",),
        )
        plan = ExecutionPlan(
            id="plan-semantic", goal="交付 API", work_items=(dependency, item),
            trace=TraceContext(requirement_id="req-semantic", trace_id="tr-semantic"),
        )
        package = build_task_input(RunState(plan=plan), item)
        contract = package.as_dict()["semantic_contract"]
        self.assertEqual(contract["predecessors"][0]["work_item_id"], "architecture")
        self.assertIn("depends_on_units", contract["fields"])
        self.assertIn("只能引用 unit_id", contract["fields"]["depends_on_units"]["value_rules"][0])

    def test_exclusive_repair_prompt_contains_structured_failure_and_workspace_protocol(self) -> None:
        item = WorkItem(
            id="repair-code",
            agent_id="code_agent",
            objective="修复 unit 失败涉及的实现文件",
            output_key="implementation_patch",
            failure_package=FailurePackage(
                signal=FailureSignal(FailureKind.TEST_FAILURE, "ImportError", "ev-test"),
                check_id="unit",
                exit_code=2,
                repair_paths=("backend/app/application/ports.py",),
                owner_files=("backend/app/application/ports.py",),
            ),
        )
        plan = ExecutionPlan(
            id="plan-repair-input",
            goal="修复项目",
            work_items=(item,),
            trace=TraceContext(requirement_id="req-repair-input", trace_id="tr-repair-input"),
        )
        package = build_task_input(RunState(plan=plan), item)
        prompt = package.as_prompt()
        self.assertEqual(package.as_dict()["failure_package"]["repair_paths"], ["backend/app/application/ports.py"])
        self.assertIn("结构化失败证据", prompt)
        self.assertIn("write_workspace_file", prompt)
        self.assertIn("backend/app/application/ports.py", prompt)

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

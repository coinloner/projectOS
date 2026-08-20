import tempfile
import unittest
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.artifact.store import ArtifactStore
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft
from app.planner.service import PlannerFailure, PlannerService
from app.planner.validator import PlanValidationError, PlanValidator
from app.workflow.template import (
    TaskBlueprint,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
)
def planning_template() -> WorkflowTemplate:
    return WorkflowTemplate(
        id="planning_baseline",
        name="规划基线",
        description="普通 Planner 依赖测试模板",
        nodes=(
            TaskBlueprint(
                id="requirement",
                agent_id="requirement_agent",
                objective="整理需求",
                output_key="requirement",
            ),
            TaskBlueprint(
                id="architecture",
                agent_id="architecture_agent",
                objective="设计架构",
                output_key="architecture",
                depends_on=("requirement",),
            ),
        ),
    )
from app.orchestration.work_item import DependencySource


class FakePlannerRuntime:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._responses)


class ScenarioPlannerRuntime:
    """用确定性场景草案验证 Planner 的动态路径选择边界。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "已有需求，请做技术架构" in prompt:
            return (
                '{"rationale":"需求已存在","template_hint_id":null,"steps":['
                '{"ref":"architecture","agent_id":"architecture_agent",'
                '"objective":"设计技术架构"}]}'
            )
        return (
            '{"rationale":"先整理想法","template_hint_id":null,"steps":['
            '{"ref":"requirement","agent_id":"requirement_agent",'
            '"objective":"整理产品需求"}]}'
        )


def build_agents() -> AgentRegistry:
    registry = AgentRegistry()
    for agent_id, domain, description, output_key in (
        ("requirement_agent", "requirement", "整理用户需求", "requirement"),
        ("architecture_agent", "architecture", "设计技术架构", "architecture"),
        ("task_agent", "task", "拆分工程任务", "tasks"),
        ("bootstrap_agent", "bootstrap", "声明项目运行时", "environment"),
        ("code_agent", "code", "生成首版代码", "implementation"),
        ("test_agent", "test", "执行基础测试", "tests"),
        ("review_agent", "review", "审查交付质量", "review"),
    ):
        registry.register(
            AgentDefinition(
                id=agent_id,
                domain=domain,
                description=description,
                output_key=output_key,
            ),
            factory=lambda: None,
        )
    return registry


def build_templates() -> WorkflowTemplateRegistry:
    registry = WorkflowTemplateRegistry()
    registry.register(planning_template())
    return registry


class PlanningContextTest(unittest.TestCase):
    def test_context_exposes_artifact_existence_not_contents(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            artifacts = ArtifactStore(project_path)
            artifacts.save("requirement", "secret requirement content")

            context = PlanningContext.build(
                goal="设计项目架构",
                agents=build_agents(),
                templates=build_templates(),
                artifacts=artifacts,
            )

        self.assertEqual(context.artifacts[0].key, "requirement")
        self.assertTrue(context.artifacts[0].exists)
        prompt_json = context.as_prompt_json()
        self.assertNotIn("secret requirement content", prompt_json)
        self.assertIn('"output_key": "architecture"', prompt_json)
        self.assertIn('"id": "planning_baseline"', prompt_json)
        self.assertIn('"implementation_file_count": 0', prompt_json)
        self.assertIn('"manifest_exists": false', prompt_json)

    def test_context_counts_real_workspace_implementation_files_not_tests(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            workspace = Path(project_path) / "workspace"
            (workspace / "tests").mkdir(parents=True)
            (workspace / "tests" / "test_app.py").write_text("pass\n")
            context = PlanningContext.build(
                goal="实现网站",
                agents=build_agents(),
                templates=build_templates(),
                artifacts=ArtifactStore(project_path),
            )
            self.assertEqual(context.workspace.implementation_file_count, 0)

            (workspace / "src").mkdir()
            (workspace / "src" / "app.py").write_text("print('ok')\n")
            implemented = PlanningContext.build(
                goal="实现网站",
                agents=build_agents(),
                templates=build_templates(),
                artifacts=ArtifactStore(project_path),
            )

        self.assertEqual(implemented.workspace.implementation_file_count, 1)


class PlanValidatorTest(unittest.TestCase):
    def test_validator_allows_repeated_agent_with_distinct_work_item_results(self) -> None:
        draft = PlanDraft.parse(
            """{
                "rationale": "将需求拆成独立的初稿和补充",
                "steps": [
                    {"ref": "draft", "agent_id": "requirement_agent", "objective": "整理核心需求"},
                    {"ref": "refine", "agent_id": "requirement_agent", "objective": "补充边界条件", "depends_on": ["draft"]}
                ]
            }"""
        )

        plan = self.validator.validate(draft, context=self.context, plan_id="repeat")

        self.assertEqual(
            [item.agent_id for item in plan.work_items],
            ["requirement_agent", "requirement_agent"],
        )
        self.assertEqual(
            [item.output_key for item in plan.work_items],
            ["requirement_01", "requirement_02"],
        )
        self.assertEqual(
            [item.artifact_key for item in plan.work_items],
            ["requirement", "requirement"],
        )
        self.assertEqual(
            plan.work_items[1].dependency_ids, (plan.work_items[0].id,)
        )
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.context = PlanningContext.build(
            goal="从零交付一个项目",
            agents=build_agents(),
            templates=build_templates(),
            artifacts=ArtifactStore(self._directory.name),
        )
        self.validator = PlanValidator()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_validator_derives_work_item_identity_and_template_dependency(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "需要先定义需求再设计架构",
                "template_hint_id": "planning_baseline",
                "steps": [
                    {
                        "ref": "requirement",
                        "agent_id": "requirement_agent",
                        "objective": "整理目标为需求文档",
                    },
                    {
                        "ref": "architecture",
                        "agent_id": "architecture_agent",
                        "objective": "根据需求设计技术架构",
                        "depends_on": [],
                    },
                ],
            }
        )

        plan = self.validator.validate(draft, context=self.context, plan_id="plan-001")

        self.assertEqual(
            [item.id for item in plan.work_items],
            ["wi-01-requirement", "wi-02-architecture"],
        )
        self.assertEqual(
            plan.work_items[1].dependency_ids,
            ("wi-01-requirement",),
        )
        self.assertEqual(
            plan.work_items[1].dependencies[0].source, DependencySource.TEMPLATE
        )
        self.assertEqual(plan.work_items[1].output_key, "architecture")

    def test_validator_rejects_unknown_agent_and_unselected_dependency(self) -> None:
        unknown_agent = PlanDraft.model_validate(
            {
                "rationale": "test",
                "steps": [{"ref": "unknown", "agent_id": "unknown_agent", "objective": "do work"}],
            }
        )
        with self.assertRaisesRegex(PlanValidationError, "未注册 Agent"):
            self.validator.validate(unknown_agent, context=self.context, plan_id="bad")

        unknown_dependency = PlanDraft.model_validate(
            {
                "rationale": "test",
                "steps": [
                    {
                        "ref": "architecture",
                        "agent_id": "architecture_agent",
                        "objective": "design",
                        "depends_on": ["requirement"],
                    }
                ],
            }
        )
        with self.assertRaisesRegex(PlanValidationError, "依赖未选择的 step ref"):
            self.validator.validate(
                unknown_dependency,
                context=self.context,
                plan_id="bad",
            )

    def test_validator_requires_code_before_test_and_review_without_workspace_code(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "验证已有实现",
                "steps": [
                    {"ref": "test", "agent_id": "test_agent", "objective": "编写测试"},
                    {
                        "ref": "review",
                        "agent_id": "review_agent",
                        "objective": "审查交付",
                        "depends_on": ["test"],
                    },
                ],
            }
        )

        with self.assertRaisesRegex(PlanValidationError, "必须先选择 code_agent"):
            self.validator.validate(draft, context=self.context, plan_id="bad")

    def test_validator_requires_bootstrap_before_code_test_review_without_runtime(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "先实现再验证和审查",
                "steps": [
                    {
                        "ref": "bootstrap",
                        "agent_id": "bootstrap_agent",
                        "objective": "声明运行环境",
                    },
                    {
                        "ref": "code",
                        "agent_id": "code_agent",
                        "objective": "实现首版代码",
                        "depends_on": ["bootstrap"],
                    },
                    {
                        "ref": "test",
                        "agent_id": "test_agent",
                        "objective": "编写并运行测试",
                        "depends_on": ["bootstrap", "code"],
                    },
                    {
                        "ref": "review",
                        "agent_id": "review_agent",
                        "objective": "审查交付",
                        "depends_on": ["code", "test"],
                    },
                ],
            }
        )

        plan = self.validator.validate(draft, context=self.context, plan_id="valid")

        self.assertEqual(
            [item.agent_id for item in plan.work_items],
            ["bootstrap_agent", "code_agent", "test_agent", "review_agent"],
        )
        test_item = plan.work_items[2]
        self.assertEqual(
            {dependency.source for dependency in test_item.dependencies},
            {DependencySource.SYSTEM},
        )

    def test_validator_allows_explicit_template_dependency_override(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "架构探索与需求文档可并行进行",
                "template_hint_id": "planning_baseline",
                "template_dependency_overrides": [
                    {
                        "predecessor_agent_id": "requirement_agent",
                        "successor_agent_id": "architecture_agent",
                        "reason": "本次仅准备技术候选方案，不读取需求正文",
                    }
                ],
                "steps": [
                    {
                        "ref": "requirement",
                        "agent_id": "requirement_agent",
                        "objective": "整理需求",
                    },
                    {
                        "ref": "architecture",
                        "agent_id": "architecture_agent",
                        "objective": "准备技术候选方案",
                    },
                ],
            }
        )

        plan = self.validator.validate(draft, context=self.context, plan_id="override")

        self.assertEqual(plan.work_items[1].dependencies, ())


class PlannerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactStore(self._directory.name)
        self.agents = build_agents()
        self.templates = build_templates()

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_planner_can_skip_existing_requirement_without_a_template(self) -> None:
        self.artifacts.save("requirement", "# Existing requirement")
        runtime = FakePlannerRuntime(
            [
                """{
                    "rationale": "需求已存在，只需设计架构",
                    "template_hint_id": null,
                    "steps": [{
                        "ref": "architecture",
                        "agent_id": "architecture_agent",
                        "objective": "基于现有需求设计架构",
                        "depends_on": []
                    }]
                }"""
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )

        result = service.plan(goal="已有需求，请做技术架构", plan_id="architecture-only")

        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.plan.template_id, None)
        self.assertEqual(
            [item.agent_id for item in result.plan.work_items], ["architecture_agent"]
        )
        self.assertTrue(result.context.artifacts[0].exists)

    def test_planner_retries_once_with_validation_feedback(self) -> None:
        runtime = FakePlannerRuntime(
            [
                '{"rationale": "bad", "steps": [{"ref": "unknown", "agent_id": "unknown", "objective": "x"}]}',
                """{
                    "rationale": "只需要整理需求",
                    "steps": [{
                        "ref": "requirement",
                        "agent_id": "requirement_agent",
                        "objective": "整理用户目标为需求文档"
                    }]
                }""",
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )

        result = service.plan(goal="整理一个产品想法", plan_id="requirement-only")

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.plan.work_items[0].agent_id, "requirement_agent")
        self.assertIn("校验错误", runtime.prompts[1])
        self.assertIn("未注册 Agent", runtime.prompts[1])

    def test_planner_fails_after_one_unsuccessful_repair(self) -> None:
        runtime = FakePlannerRuntime(
            [
                '{"rationale": "bad", "steps": [{"ref": "missing", "agent_id": "missing", "objective": "x"}]}',
                '{"rationale": "still bad", "steps": [{"ref": "missing", "agent_id": "missing", "objective": "x"}]}',
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )

        with self.assertRaisesRegex(PlannerFailure, "修复后"):
            service.plan(goal="test", plan_id="invalid")

    def test_planner_creates_a_new_plan_for_a_trusted_failure_signal(self) -> None:
        runtime = FakePlannerRuntime(
            [
                '{"rationale": "初始", "steps": [{"ref": "requirement", "agent_id": "requirement_agent", "objective": "整理需求"}]}',
                '{"rationale": "准备环境、修复并重跑验证", "steps": [{"ref": "bootstrap", "agent_id": "bootstrap_agent", "objective": "准备受控运行时"}, {"ref": "fix", "agent_id": "code_agent", "objective": "修复失败原因", "depends_on": ["bootstrap"]}, {"ref": "verify", "agent_id": "test_agent", "objective": "重新验证修复", "depends_on": ["fix"]}]}',
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )
        initial = service.plan(goal="测试", plan_id="initial")

        from app.orchestration.retry import FailureKind, FailureSignal

        repair = service.plan_repair(
            previous_plan=initial.plan,
            failure=FailureSignal(FailureKind.TEST_FAILURE, "assertion failed", "ev-1"),
            plan_id="initial-repair-1",
        )

        self.assertEqual(repair.plan.trace, initial.plan.trace)
        self.assertEqual(repair.plan.id, "initial-repair-1")
        self.assertEqual(
            [item.agent_id for item in repair.plan.work_items],
            ["bootstrap_agent", "code_agent", "test_agent"],
        )
        self.assertIsNone(repair.plan.work_items[0].failure_package)
        self.assertEqual(
            repair.plan.work_items[1].failure_package.signal.evidence_id, "ev-1"
        )
        self.assertIn("可信控制面数据", runtime.prompts[1])

    def test_planner_selects_different_dag_paths_for_different_goals(self) -> None:
        runtime = ScenarioPlannerRuntime()
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )

        ideation = service.plan(goal="整理一个产品想法", plan_id="scenario-ideation")
        architecture = service.plan(
            goal="已有需求，请做技术架构", plan_id="scenario-architecture"
        )

        self.assertEqual(
            [item.agent_id for item in ideation.plan.work_items],
            ["requirement_agent"],
        )
        self.assertEqual(
            [item.agent_id for item in architecture.plan.work_items],
            ["architecture_agent"],
        )
        self.assertNotEqual(
            tuple(item.agent_id for item in ideation.plan.work_items),
            tuple(item.agent_id for item in architecture.plan.work_items),
        )


if __name__ == "__main__":
    unittest.main()

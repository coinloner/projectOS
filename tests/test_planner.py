import tempfile
import unittest
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.artifact.store import ArtifactStore
from app.bootstrap.runtime import build_container
from app.planner.context import PlanningContext
from app.planner.draft import PlanDraft, PlanDraftError
from app.planner.service import PlannerFailure, PlannerService
from app.planner.validator import PlanValidationError, PlanValidator
from app.orchestration.plan import ExecutionPlan
from app.orchestration.retry import (
    FailureKind,
    FailurePackage,
    FailureSignal,
    RecoveryAction,
    RetryLedger,
    RetryScope,
)
from app.orchestration.trace import TraceContext
from app.orchestration.work_item import WorkItem
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
    def test_plan_draft_parse_accepts_zero_width_transport_prefix(self) -> None:
        draft = PlanDraft.parse(
            '\u200b{"rationale":"ok","steps":[],"template_hint_id":null}'
        )
        self.assertEqual(draft.rationale, "ok")

    def test_plan_draft_rejects_controlled_responses_envelope(self) -> None:
        with self.assertRaises(PlanDraftError):
            PlanDraft.parse(
                '{"kind":"PlanDraft","version":"1.0",'
                '"process_id":"software_delivery","template_id":"project_delivery",'
                '"execution_mode":"parallel","nodes":[]}'
            )

    def test_plan_draft_rejects_legacy_type_discriminator(self) -> None:
        with self.assertRaises(PlanDraftError):
            PlanDraft.parse(
                '\u200b{"type":"PlanDraft","version":"1.0",'
                '"process_id":"software_delivery","template_id":"project_delivery",'
                '"status":"blocked_pending_capability","nodes":[]}'
            )

    def test_plan_draft_rejects_unsafe_nodes_in_legacy_envelope(self) -> None:
        with self.assertRaises(PlanDraftError):
            PlanDraft.parse(
                '{"kind":"PlanDraft","template_id":"custom", "nodes":['
                '{"id":"requirement","type":"agent", "agent_id":"requirement_agent", "objective":"整理需求"},'
                '{"id":"tool","type":"tool_call", "tool":"search_docs"}'
                ']}'
            )

    def test_plan_draft_rejects_agent_only_legacy_envelope(self) -> None:
        with self.assertRaises(PlanDraftError):
            PlanDraft.parse(
                '{"kind":"PlanDraft","template_id":"custom", "nodes":['
                '{"id":"requirement","type":"agent", "agent_id":"requirement_agent", "objective":"整理需求"},'
                '{"id":"architecture","type":"agent", "agent_id":"architecture_agent", "objective":"设计架构", "depends_on":["requirement"]}'
                ']}'
            )

    def test_empty_complex_delivery_uses_coarse_controlled_route_before_llm(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            runtime = FakePlannerRuntime([
                '{"rationale":"should not be called","template_hint_id":null,"steps":[]}'
            ])
            container = build_container(project_path, planner_runtime=runtime)
            result = container.planner.plan(
                goal="生产级前后端系统，包含数据库、异步任务和并发交互页面",
                plan_id="complex-delivery",
            )
            self.assertEqual(result.plan.template_id, "project_delivery_dynamic")
            self.assertEqual(result.attempts, 0)
            self.assertEqual(runtime.prompts, [])

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
        self.assertIn('"software_delivery"', prompt_json)

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
    def test_validator_rejects_unknown_process(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "x",
                "process_id": "missing-process",
                "steps": [
                    {"ref": "requirement", "agent_id": "requirement_agent", "objective": "整理需求"}
                ],
            }
        )
        with self.assertRaises(PlanValidationError):
            self.validator.validate(draft, context=self.context, plan_id="plan-process")

    def test_plan_carries_process_id_even_for_legacy_template(self) -> None:
        draft = PlanDraft.model_validate(
            {
                "rationale": "x",
                "template_hint_id": "planning_baseline",
                "steps": [
                    {"ref": "requirement", "agent_id": "requirement_agent", "objective": "整理需求"}
                ],
            }
        )
        plan = self.validator.validate(draft, context=self.context, plan_id="plan-process")
        self.assertEqual(plan.process_id, "software_delivery")

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
    def test_empty_goal_uses_planner_failure_contract(self) -> None:
        service = PlannerService(
            runtime=FakePlannerRuntime([]),
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )
        with self.assertRaisesRegex(PlannerFailure, "goal 不能为空"):
            service.plan(goal="  ", plan_id="empty-goal")

    def test_repair_package_sets_code_allowed_paths(self) -> None:
        service = PlannerService(
            runtime=FakePlannerRuntime([]),
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )
        package = FailurePackage(
            signal=FailureSignal(FailureKind.TEST_FAILURE, "unit failed", "ev-1"),
            check_id="unit",
            repair_paths=("backend/app/infrastructure/repositories.py",),
        )
        repair_plan = ExecutionPlan(
            id="repair-plan",
            goal="修复",
            trace=TraceContext(requirement_id="req-repair", trace_id="tr-repair"),
            work_items=(WorkItem(
                id="repair-code",
                agent_id="code_agent",
                objective="修复实现",
                output_key="implementation_patch",
            ),),
        )
        attached = service._attach_failure_package(repair_plan, package)
        item = attached.work_items[0]
        self.assertEqual(item.allowed_paths, ("backend/app/infrastructure/repositories.py",))
        self.assertIn("tests/**", item.forbidden_paths)
        self.assertEqual(item.failure_package.repair_paths, item.allowed_paths)

    def test_repair_plan_rejects_unscoped_code_agent(self) -> None:
        runtime = FakePlannerRuntime(
            [
                '{"rationale":"修复实现","base_plan_id":"initial","repair_scope":[],"operations":['
                '{"operation":"add","ref":"code-fix","agent_id":"code_agent",'
                '"objective":"通过 write_workspace_file 修复实现","depends_on":[]}]}',
                '{"rationale":"再次修复实现","base_plan_id":"initial","repair_scope":[],"operations":['
                '{"operation":"add","ref":"code-fix","agent_id":"code_agent",'
                '"objective":"通过 write_workspace_file 修复实现","depends_on":[]}]}',
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )
        previous = ExecutionPlan(
            id="initial",
            goal="修复活动平台",
            trace=TraceContext(requirement_id="req-repair", trace_id="tr-repair-empty-scope"),
            work_items=(
                WorkItem(
                    id="tests",
                    agent_id="test_agent",
                    objective="运行测试",
                    output_key="tests",
                ),
            ),
        )

        with self.assertRaisesRegex(PlannerFailure, "缺少控制面归因"):
            service.plan_repair(
                previous_plan=previous,
                failure=FailureSignal(FailureKind.TEST_FAILURE, "unit failed"),
                plan_id="repair-empty-scope",
            )

        self.assertIn("缺少控制面归因", runtime.prompts[1])

    def test_repair_retry_preserves_repair_only_agent_boundary(self) -> None:
        """第一次草案误带 review 时，第二次仍必须使用修复专用约束。"""
        runtime = FakePlannerRuntime(
            [
                '{"rationale":"误把交付审查加入修复","base_plan_id":"initial","repair_scope":[],"operations":['
                '{"operation":"add","ref":"bootstrap","agent_id":"bootstrap_agent","objective":"确认环境"},'
                '{"operation":"add","ref":"code-fix","agent_id":"code_agent","objective":"通过 write_workspace_file 修复实现","depends_on":["bootstrap"]},'
                '{"operation":"add","ref":"test-fix","agent_id":"test_agent","objective":"通过 write_test_file 复验修复","depends_on":["code-fix"]},'
                '{"operation":"add","ref":"review","agent_id":"review_agent","objective":"审查结果","depends_on":["test-fix"]}]}',
                '{"rationale":"修复并复验","base_plan_id":"initial","repair_scope":[],"operations":['
                '{"operation":"add","ref":"bootstrap","agent_id":"bootstrap_agent","objective":"确认环境"},'
                '{"operation":"add","ref":"code-fix","agent_id":"code_agent","objective":"通过 write_workspace_file 修复实现","depends_on":["bootstrap"]},'
                '{"operation":"add","ref":"test-fix","agent_id":"test_agent","objective":"通过 write_test_file 复验修复","depends_on":["code-fix"]}]}',
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )
        previous = ExecutionPlan(
            id="initial",
            goal="修复活动平台",
            trace=TraceContext(requirement_id="req-repair", trace_id="tr-repair-retry"),
            work_items=(
                WorkItem(
                    id="tests",
                    agent_id="test_agent",
                    objective="运行测试",
                    output_key="tests",
                ),
            ),
        )

        result = service.plan_repair(
            previous_plan=previous,
            failure=FailureSignal(
                FailureKind.TEST_FAILURE,
                "unit failed: workspace/backend/app/services/activity.py",
            ),
            plan_id="repair-retry",
        )

        self.assertEqual(result.attempts, 2)
        self.assertEqual(
            [item.agent_id for item in result.plan.work_items],
            ["bootstrap_agent", "code_agent", "test_agent"],
        )
        self.assertIn("禁止 code_integration_agent、review_agent", runtime.prompts[1])
        self.assertIn("修复计划不能包含 integration/review/planning Agent", runtime.prompts[1])

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

    def test_planner_retries_legacy_envelope_into_new_protocol(self) -> None:
        """A legacy Responses envelope is diagnostic input, never a retry schema."""
        runtime = FakePlannerRuntime(
            [
                '{"kind":"PlanDraft","version":"1.0",'
                '"template_id":"planning_baseline","nodes":[]}',
                '{"rationale":"整理需求","process_id":null,"template_hint_id":null,'
                '"steps":[{"ref":"requirement","agent_id":"requirement_agent",'
                '"objective":"整理用户需求","depends_on":[]}],'
                '"template_dependency_overrides":[]}',
            ]
        )
        service = PlannerService(
            runtime=runtime,
            agents=self.agents,
            templates=self.templates,
            artifacts=self.artifacts,
        )

        result = service.plan(goal="整理一个产品想法", plan_id="legacy-retry")

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.draft.steps[0].agent_id, "requirement_agent")
        retry_prompt = runtime.prompts[1]
        self.assertIn('"rationale"', retry_prompt)
        self.assertIn('"steps"', retry_prompt)
        self.assertIn('"template_dependency_overrides"', retry_prompt)
        self.assertIn("上一份草案仅作为错误诊断资料", retry_prompt)
        for forbidden in ("kind", "type", "version", "template_id", "nodes", "execution_mode", "tool_call"):
            self.assertIn(forbidden, retry_prompt)
        self.assertIn('"kind":"PlanDraft"', retry_prompt)

    def test_empty_delivery_uses_controlled_template_before_dynamic_planner(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            container = build_container(project_path)
            runtime = FakePlannerRuntime(
                [
                    '{"rationale":"shortcut","template_hint_id":null,"steps":['
                    '{"ref":"implementation","agent_id":"code_agent","objective":"实现代码"},'
                    '{"ref":"tests","agent_id":"test_agent","objective":"运行测试"},'
                    '{"ref":"review","agent_id":"review_agent","objective":"审查交付"}]}' ,
                    '{"rationale":"完整交付","template_hint_id":"project_delivery","steps":[]}',
                ]
            )
            service = PlannerService(
                runtime=runtime,
                agents=container.agents,
                templates=container.templates,
                artifacts=container.artifacts,
            )

            result = service.plan(goal="实现 Todo 并完成测试和交付审查", plan_id="full-delivery")

            self.assertEqual(result.attempts, 0)
            self.assertEqual(result.plan.template_id, "project_delivery_dynamic")
            self.assertEqual(len(result.plan.work_items), 4)
            self.assertEqual(
                {item.artifact_key for item in result.plan.work_items},
                {"requirement", "architecture", "architecture_contract"},
            )
            self.assertEqual(runtime.prompts, [])

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

        with self.assertRaisesRegex(PlannerFailure, "修复后") as raised:
            service.plan(goal="test", plan_id="invalid")

        records = RetryLedger(
            self._directory.name, raised.exception.trace_id
        ).records()
        self.assertEqual([record.scope for record in records], [RetryScope.PLANNING] * 2)
        self.assertEqual([record.attempt for record in records], [1, 2])
        self.assertEqual(
            [record.action for record in records],
            [RecoveryAction.RETRY_ITEM, RecoveryAction.FAIL],
        )

    def test_planner_creates_a_new_plan_for_a_trusted_failure_signal(self) -> None:
        runtime = FakePlannerRuntime(
            [
                '{"rationale": "初始", "steps": [{"ref": "requirement", "agent_id": "requirement_agent", "objective": "整理需求"}]}',
                '{"rationale": "准备环境、修复并重跑验证", "base_plan_id": "initial", "repair_scope": [], "operations": [{"operation":"add", "ref": "bootstrap", "agent_id": "bootstrap_agent", "objective": "准备受控运行时"}, {"operation":"add", "ref": "fix", "agent_id": "code_agent", "objective": "修复失败原因", "depends_on": ["bootstrap"]}, {"operation":"add", "ref": "verify", "agent_id": "test_agent", "objective": "重新验证修复", "depends_on": ["fix"]}]}',
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
            failure=FailureSignal(
                FailureKind.TEST_FAILURE,
                "assertion failed: workspace/backend/app/services/orders.py",
            ),
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
            repair.plan.work_items[1].failure_package.repair_paths,
            ("backend/app/services/orders.py",),
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

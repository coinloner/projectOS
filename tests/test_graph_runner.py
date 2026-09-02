import unittest
from types import SimpleNamespace
from threading import Barrier
import tempfile
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.memory.store import MemoryStore
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.orchestration.retry import FailureKind, FailurePackage, FailureSignal, RecoveryAction, RetryPolicy
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import (
    GraphRunner,
    GraphRunStatus,
    _architecture_tool_allowlist,
    _expected_architecture_tool,
    _partitioned_code_retry_prompt,
    _refresh_review_quality_section,
    _summarize_sandbox_evidence,
)
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.trace import TraceContext, TraceStore
from app.workflow.template import (
    TaskBlueprint,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
)
from app.workflow.templates import project_delivery_template
from app.sandbox.result import SandboxResult, SandboxStatus
from app.orchestration.work_item import (
    DependencySource,
    WorkItem,
    WorkItemDependency,
)


class FakeAgent:
    def __init__(self, result: AgentResult | Exception) -> None:
        self._result = result
        self.tasks: list[str] = []

    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        self.tasks.append(task)
        self.context = context
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeMCPClient:
    def __init__(self) -> None:
        self.discoveries = 0

    def list_tools(self) -> list[dict]:
        self.discoveries += 1
        return []

    def call_tool(self, name: str, arguments: dict) -> str:
        return "unused"


class ReviewQualityRefreshTest(unittest.TestCase):
    def test_refresh_replaces_stale_quality_appendix(self) -> None:
        old = (
            "# 交付审查\n\n## 审查结论\nBLOCKED\n\n"
            "## 确定性质量检查\n```text\nstatus=failed\n```\n\n"
            "## 阻塞问题\n旧报告"
        )
        refreshed = _refresh_review_quality_section(
            old, "policy_id=project.quality.v1\nstatus=passed\nissues=0"
        )
        self.assertEqual(refreshed.count("## 确定性质量策略"), 1)
        self.assertIn("status=passed", refreshed)
        self.assertNotIn("status=failed", refreshed)
        self.assertIn("## 审查结论\nPASS", refreshed)

    def test_sandbox_summary_keeps_latest_result_per_check(self) -> None:
        evidence = [
            SimpleNamespace(
                id="old", work_item_id="tests", check_id="unit", status=SimpleNamespace(value="failed"),
                exit_code=1, created_at="2026-08-29T10:00:00Z", stdout="FAILED old", stderr="",
            ),
            SimpleNamespace(
                id="new", work_item_id="repair-tests", check_id="unit", status=SimpleNamespace(value="passed"),
                exit_code=0, created_at="2026-08-29T11:00:00Z", stdout="", stderr="",
            ),
        ]
        summary = _summarize_sandbox_evidence(evidence)
        self.assertIn("evidence_id=new", summary)
        self.assertNotIn("evidence_id=old", summary)

    def test_provider_transport_failure_is_classified_for_bounded_retry(self) -> None:
        agents = AgentRegistry()
        agents.register(
            AgentDefinition("requirement_agent", "requirement", "需求", "requirement"),
            lambda: FakeAgent(RuntimeError("peer closed connection without sending complete message body")),
        )
        result = GraphRunner(agents, ToolGateway()).run(
            ExecutionPlan(
                id="provider-failure",
                goal="测试 Provider",
                trace=TraceContext.ephemeral(),
                work_items=(make_node("provider", agent_id="requirement_agent"),),
            )
        )
        self.assertEqual(result.status, GraphRunStatus.FAILED)
        self.assertIn("provider_transport", result.error or "")


def make_node(
    node_id: str,
    *,
    agent_id: str = "requirement_agent",
    output_key: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> WorkItem:
    return WorkItem(
        id=node_id,
        agent_id=agent_id,
        objective=f"完成 {node_id}",
        output_key=output_key or f"{node_id}_output",
        dependencies=tuple(
            WorkItemDependency(
                work_item_id=dependency,
                source=DependencySource.PLANNER,
            )
            for dependency in depends_on
        ),
    )


def plan_from_template(template: WorkflowTemplate) -> ExecutionPlan:
    return ExecutionPlan(
        id=f"{template.id}-execution",
        goal="从零交付一个项目",
        template_id=template.id,
        work_items=tuple(
            WorkItem(
                id=blueprint.id,
                agent_id=blueprint.agent_id,
                objective=blueprint.objective,
                output_key=blueprint.output_key,
                dependencies=tuple(
                    WorkItemDependency(
                        work_item_id=dependency,
                        source=DependencySource.TEMPLATE,
                    )
                    for dependency in blueprint.depends_on
                ),
            )
            for blueprint in template.nodes
        ),
    )


class ArchitectureToolNarrowingTest(unittest.TestCase):
    def test_layered_slots_have_one_expected_writer(self) -> None:
        cases = (
            ("architecture-blueprint", "blueprint", "write_architecture_blueprint"),
            ("architecture-module-api", "module-api", "write_module_design"),
            (
                "architecture-implementation-api",
                "implementation-api",
                "write_implementation_design",
            ),
        )
        for item_id, slot, expected in cases:
            item = WorkItem(
                id=item_id,
                agent_id="architecture_agent",
                objective="架构设计",
                output_key="architecture",
                execution_mode=ExecutionMode.PARTITIONED,
                slot=slot,
            )
            self.assertEqual(
                _architecture_tool_allowlist(item),
                ("load_architecture_input", expected),
            )
            self.assertEqual(_expected_architecture_tool(item), expected)

    def test_unscoped_architecture_slots_require_explicit_tool_contract(self) -> None:
        item = WorkItem(
            id="architecture-api",
            agent_id="architecture_agent",
            objective="架构 API 决策包",
            output_key="architecture_api",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="api",
        )
        self.assertEqual(_architecture_tool_allowlist(item), ())


class GraphRunnerTest(unittest.TestCase):
    def test_partitioned_code_retry_prompt_is_write_first_and_compact(self) -> None:
        item = WorkItem(
            id="wi-code-interface-dependencies",
            agent_id="code_agent",
            objective="实现接口依赖文件",
            output_key="code-interface-dependencies",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="backend",
            implementation_unit_id="u-interface-dependencies",
            owned_files=("backend/app/interface/dependencies.py",),
            required_paths=("backend/app/interface/dependencies.py",),
            allowed_paths=("backend/app/interface/**",),
            forbidden_paths=("backend/app/domain/**", ".projectos/**"),
            input_refs=(
                ArtifactRef.published("architecture"),
                ArtifactRef.staged(
                    artifact_key="code-ports",
                    trace_id="tr-test",
                    work_item_id="wi-code-ports",
                    slot="backend",
                ),
            ),
        )
        prompt = _partitioned_code_retry_prompt(
            item, failure_reason="没有生成 Git ChangeSet"
        )
        self.assertIn("write_staged_code_file", prompt)
        self.assertIn("backend/app/interface/dependencies.py", prompt)
        self.assertIn("没有生成 Git ChangeSet", prompt)
        self.assertNotIn('"task_input"', prompt)
        self.assertNotIn("重新设计", prompt)

    def test_code_delivery_failure_has_dedicated_retry_budget(self) -> None:
        signal = FailureSignal(FailureKind.CODE_DELIVERY_INCOMPLETE, "缺少 ChangeSet")
        policy = RetryPolicy()
        self.assertEqual(
            policy.action_for(signal, total_retries=0, item_retries=0, kind_retries=0),
            RecoveryAction.RETRY_ITEM,
        )
    def test_code_delivery_checks_python_syntax_and_entrypoint_symbol(self) -> None:
        from app.orchestration.runner import GraphRunner

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace" / "backend" / "app"
            path.mkdir(parents=True)
            (path / "main.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")
            change = SimpleNamespace(worktree_path=directory)
            item = SimpleNamespace(
                delivery_contract={"entrypoints": {"backend_file": "backend/app/main.py"}}
            )
            issues = GraphRunner._validate_delivered_content(
                item, change, {"backend/app/main.py"}
            )
            self.assertIn("入口文件缺少必需符号", " ".join(issues))

    def test_code_delivery_checks_declared_public_symbols(self) -> None:
        from app.orchestration.runner import GraphRunner

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace" / "backend" / "domain"
            path.mkdir(parents=True)
            (path / "order.py").write_text(
                "class Order:\n    pass\n", encoding="utf-8"
            )
            change = SimpleNamespace(worktree_path=directory)
            item = SimpleNamespace(
                delivery_contract={"provided_symbols": ["Order", "Order.total"]}
            )
            issues = GraphRunner._validate_delivered_content(
                item, change, {"backend/domain/order.py"}
            )
            self.assertIn("交付文件缺少合同声明符号", " ".join(issues))

    def setUp(self) -> None:
        self.tools = ToolGateway()
        self.agents = AgentRegistry()

    def register_agent(
        self,
        agent_id: str,
        result: AgentResult | Exception,
        *,
        domain: str = "requirement",
        max_parallel_instances: int = 1,
    ) -> list[FakeAgent]:
        created: list[FakeAgent] = []

        def factory() -> FakeAgent:
            agent = FakeAgent(result)
            created.append(agent)
            return agent

        self.agents.register(
            AgentDefinition(
                id=agent_id,
                domain=domain,
                description=f"{agent_id} description",
                output_key=f"{agent_id}_output",
                max_parallel_instances=max_parallel_instances,
            ),
            factory=factory,
        )
        return created

    def test_runner_exposes_dependency_artifact_keys_without_inlining_content(self) -> None:
        first_agents = self.register_agent(
            "requirement_agent", AgentResult.completed("需求草稿")
        )
        second_agents = self.register_agent(
            "architecture_agent", AgentResult.completed("架构方案")
        )
        plan = ExecutionPlan(
            id="two-node-plan",
            goal="生成需求和架构",
            work_items=(
                make_node("requirement", output_key="requirement_draft"),
                make_node(
                    "architecture",
                    agent_id="architecture_agent",
                    output_key="architecture_design",
                    depends_on=("requirement",),
                ),
            ),
        )

        result = GraphRunner(self.agents, self.tools).run(plan)

        self.assertEqual(result.status, GraphRunStatus.COMPLETED)
        self.assertEqual(result.state.artifacts["requirement_draft"], "需求草稿")
        self.assertEqual(result.state.artifacts["architecture_design"], "架构方案")
        self.assertIn("总体目标：生成需求和架构", second_agents[0].tasks[0])
        self.assertIn("可用前置产物", second_agents[0].tasks[0])
        self.assertIn("- requirement_draft", second_agents[0].tasks[0])
        self.assertNotIn("需求草稿", second_agents[0].tasks[0])
        self.assertEqual(second_agents[0].context.trace_id, plan.trace.trace_id)
        self.assertEqual(second_agents[0].context.work_item_id, "architecture")
        self.assertEqual(second_agents[0].context.agent_id, "architecture_agent")
        self.assertEqual(len(first_agents), 1)

    def test_runner_records_bounded_execution_memory_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.register_agent("requirement_agent", AgentResult.completed("需求结果"))
            plan = ExecutionPlan(
                id="memory-plan",
                goal="记录执行上下文",
                work_items=(make_node("requirement"),),
            )

            result = GraphRunner(
                self.agents,
                self.tools,
                memory=MemoryStore(directory),
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)
            events = MemoryStore(directory).events(plan.trace.trace_id)
            self.assertEqual(
                [event.event_type for event in events],
                [
                    "agent_input",
                    "agent_output",
                    "work_item_result",
                    "checkpoint",
                    "run_summary",
                ],
            )
            self.assertEqual(events[1].content, "需求结果")
            self.assertEqual(events[2].event_type, "work_item_result")
            self.assertIn("completed_work_items", events[3].content)
            self.assertEqual(events[4].tier, "episodic")

    def test_runner_fails_for_an_unregistered_agent(self) -> None:
        plan = ExecutionPlan(
            id="unknown-agent-plan",
            goal="测试",
            work_items=(make_node("requirement"),),
        )

        result = GraphRunner(self.agents, self.tools).run(plan)

        self.assertEqual(result.status, GraphRunStatus.FAILED)
        self.assertIn("未注册 Agent", result.error)
        self.assertEqual(result.node_result.work_item_id, "requirement")

    def test_runner_returns_waiting_for_capability_without_discovering_mcp(self) -> None:
        self.register_agent(
            "requirement_agent",
            AgentResult.needs_capability(
                "external_research", "需要查询最新行业规范"
            ),
        )
        client = FakeMCPClient()
        self.tools.register_source(
            "requirement",
            "docs-mcp",
            MCPToolSource(client),
            capability="external_research",
        )
        plan = ExecutionPlan(
            id="capability-plan",
            goal="按最新规范生成需求",
            work_items=(make_node("requirement"),),
        )

        result = GraphRunner(self.agents, self.tools).run(plan)

        self.assertEqual(
            result.status, GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL
        )
        self.assertEqual(result.candidate_sources[0].name, "docs-mcp")
        self.assertEqual(client.discoveries, 0)

    def test_runner_blocks_when_no_source_can_satisfy_capability(self) -> None:
        self.register_agent(
            "requirement_agent",
            AgentResult.needs_capability("external_research", "需要资料"),
        )
        plan = ExecutionPlan(
            id="blocked-plan",
            goal="按最新规范生成需求",
            work_items=(make_node("requirement"),),
        )

        result = GraphRunner(self.agents, self.tools).run(plan)

        self.assertEqual(result.status, GraphRunStatus.BLOCKED)
        self.assertIn("external_research", result.error)

    def test_runner_converts_agent_exception_to_failed_node_result(self) -> None:
        self.register_agent("requirement_agent", RuntimeError("LLM unavailable"))
        plan = ExecutionPlan(
            id="failure-plan",
            goal="测试失败",
            work_items=(make_node("requirement"),),
        )

        result = GraphRunner(self.agents, self.tools).run(plan)

        self.assertEqual(result.status, GraphRunStatus.FAILED)
        self.assertIn("LLM unavailable", result.error)
        self.assertEqual(result.node_result.work_item_id, "requirement")

    def test_runner_runs_independent_instances_of_the_same_parallel_agent_together(self) -> None:
        barrier = Barrier(2)
        created: list[FakeAgent] = []

        class ParallelAgent(FakeAgent):
            def run(
                self, task: str, *, context: ExecutionContext | None = None
            ) -> AgentResult:
                barrier.wait(timeout=1)
                return super().run(task, context=context)

        def factory() -> ParallelAgent:
            agent = ParallelAgent(AgentResult.completed("analysis"))
            created.append(agent)
            return agent

        self.agents.register(
            AgentDefinition(
                id="analysis_agent",
                domain="analysis",
                description="独立分析任务",
                output_key="analysis",
                max_parallel_instances=2,
            ),
            factory=factory,
        )
        plan = ExecutionPlan(
            id="parallel-analysis",
            goal="并行分析两个独立问题",
            work_items=(
                make_node("analysis-a", agent_id="analysis_agent"),
                make_node("analysis-b", agent_id="analysis_agent"),
            ),
        )

        result = GraphRunner(self.agents, self.tools, max_workers=2).run(plan)

        self.assertEqual(result.status, GraphRunStatus.COMPLETED)
        self.assertEqual(len(created), 2)

    def test_test_agent_cannot_complete_without_sandbox_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("验证测试证据强制要求")
            created = self.register_agent(
                "test_agent",
                AgentResult.completed("测试已完成"),
                domain="test",
            )
            plan = ExecutionPlan(
                id="test-evidence-required",
                goal="运行测试",
                trace=trace,
                work_items=(make_node("test", agent_id="test_agent"),),
            )

            result = GraphRunner(self.agents, self.tools, traces=traces).run(plan)

            self.assertEqual(result.status, GraphRunStatus.FAILED)
            self.assertIn("test_evidence_missing", result.error)
            self.assertEqual(len(created), 2)

    def test_setup_failed_evidence_blocks_before_review(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("测试环境缺失仍需交付审查")

            class TestWithSetupFailure:
                def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
                    assert context is not None
                    traces.record_sandbox_evidence(
                        context,
                        SandboxResult(
                            status=SandboxStatus.SETUP_FAILED,
                            check_id="unit",
                            runtime_profile="python-stdlib",
                            exit_code=127,
                            duration_ms=0,
                            message="python:3.12-slim 未预置",
                        ),
                    )
                    return AgentResult.completed("已保存测试报告")

            class ReviewRecorder:
                def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
                    return AgentResult.completed("## 审查结论\n\nBLOCKED")

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("test_agent", "test", "test", "tests"),
                factory=TestWithSetupFailure,
            )
            agents.register(
                AgentDefinition("review_agent", "review", "review", "review"),
                factory=ReviewRecorder,
            )
            plan = ExecutionPlan(
                id="setup-failure-review",
                goal="测试环境缺失仍需交付审查",
                trace=trace,
                work_items=(
                    make_node("test", agent_id="test_agent", output_key="tests"),
                    make_node("review", agent_id="review_agent", output_key="review", depends_on=("test",)),
                ),
            )

            result = GraphRunner(agents, ToolGateway(), traces=traces).run(plan)

            self.assertEqual(result.status, GraphRunStatus.BLOCKED)
            self.assertIn("测试 sandbox 未就绪", result.error)
            self.assertNotIn("review", result.state.artifacts)

    def test_test_capability_request_is_recorded_as_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("测试节点误报外部能力")
            agents = AgentRegistry()
            agents.register(
                AgentDefinition("test_agent", "test", "test", "tests"),
                factory=lambda: FakeAgent(AgentResult.needs_capability("external_research", "不应联网")),
            )
            agents.register(
                AgentDefinition("review_agent", "review", "review", "review"),
                factory=lambda: FakeAgent(AgentResult.completed("BLOCKED")),
            )
            plan = ExecutionPlan(
                id="test-capability-normalized",
                goal="测试节点误报外部能力",
                trace=trace,
                work_items=(
                    make_node("test", agent_id="test_agent", output_key="tests"),
                    make_node("review", agent_id="review_agent", output_key="review", depends_on=("test",)),
                ),
            )

            result = GraphRunner(agents, ToolGateway(), traces=traces).run(plan)

            self.assertEqual(result.status, GraphRunStatus.BLOCKED)
            evidence = traces.list_sandbox_evidence(
                ExecutionContext(trace_id=trace.trace_id, work_item_id="test", agent_id="test_agent")
            )
            self.assertEqual(evidence[-1].status, SandboxStatus.SETUP_FAILED)

    def test_code_staging_capability_request_becomes_bounded_retry(self) -> None:
        """A hallucinated local write-tool gap must not dead-end the run."""
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("代码暂存工具误报")
            created: list[FakeAgent] = []

            def factory() -> FakeAgent:
                agent = FakeAgent(
                    AgentResult.needs_capability(
                        "write_staged_code_file", "当前未提供 write_staged_code_file 工具"
                    )
                )
                created.append(agent)
                return agent

            agents = AgentRegistry()
            agents.register(
                AgentDefinition(
                    "code_agent", "code", "code", "implementation", max_parallel_instances=1
                ),
                factory=factory,
            )
            plan = ExecutionPlan(
                id="code-capability-retry",
                goal="代码暂存工具误报",
                trace=trace,
                work_items=(
                    WorkItem(
                        id="code",
                        agent_id="code_agent",
                        objective="写入完整文件",
                        output_key="implementation_code",
                        execution_mode=ExecutionMode.PARTITIONED,
                        slot="backend",
                        implementation_unit_id="unit",
                        allowed_paths=("backend/app.py",),
                        required_paths=("backend/app.py",),
                        owned_files=("backend/app.py",),
                    ),
                ),
            )
            result = GraphRunner(agents, ToolGateway(), traces=traces).run(plan)
            self.assertEqual(result.status, GraphRunStatus.FAILED)
            self.assertEqual(len(created), 3)
            events = traces.list_events(trace.trace_id)
            self.assertEqual(
                [event["type"] for event in events if event["type"] == "work_item_waiting_capability"],
                [],
            )

    def test_architecture_scopes_integrate_then_quality_gate_promotes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            repository = ArtifactRepository(project_path)
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace = TraceContext(requirement_id="req-architecture", trace_id="tr-architecture")

            class ArchitectureWorkflowAgent:
                def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
                    assert context is not None
                    if context.execution_mode is ExecutionMode.PARTITIONED:
                        workflow.write_staged(context, f"# {context.slot}")
                    elif context.execution_mode is ExecutionMode.INTEGRATION:
                        workflow.create_candidate(context, "# Integrated architecture")
                    return AgentResult.completed("done")

            self.agents.register(
                AgentDefinition(
                    id="architecture_agent", domain="architecture", description="architecture",
                    output_key="architecture", max_parallel_instances=2,
                ),
                factory=ArchitectureWorkflowAgent,
            )
            api_ref = ArtifactRef.staged(
                artifact_key="architecture", trace_id=trace.trace_id,
                work_item_id="architecture-api", slot="api",
            )
            data_ref = ArtifactRef.staged(
                artifact_key="architecture", trace_id=trace.trace_id,
                work_item_id="architecture-data", slot="data",
            )
            plan = ExecutionPlan(
                id="architecture-pilot", goal="并行产出架构分区", trace=trace,
                work_items=(
                    WorkItem(
                        id="architecture-api", agent_id="architecture_agent", objective="设计 API",
                        output_key="architecture_api", artifact_key="architecture",
                        execution_mode=ExecutionMode.PARTITIONED, slot="api",
                    ),
                    WorkItem(
                        id="architecture-data", agent_id="architecture_agent", objective="设计数据层",
                        output_key="architecture_data", artifact_key="architecture",
                        execution_mode=ExecutionMode.PARTITIONED, slot="data",
                    ),
                    WorkItem(
                        id="architecture-integration", agent_id="architecture_agent", objective="整合分区",
                        output_key="architecture_candidate", artifact_key="architecture",
                        dependencies=(
                            WorkItemDependency("architecture-api", DependencySource.SYSTEM),
                            WorkItemDependency("architecture-data", DependencySource.SYSTEM),
                        ),
                        execution_mode=ExecutionMode.INTEGRATION, publish_target="architecture",
                        input_refs=(api_ref, data_ref),
                    ),
                    WorkItem(
                        id="architecture-quality-gate", agent_id="system_quality_gate",
                        objective="验证并发布候选", output_key="architecture_published",
                        artifact_key="architecture",
                        dependencies=(
                            WorkItemDependency("architecture-integration", DependencySource.SYSTEM),
                        ),
                        execution_mode=ExecutionMode.QUALITY_GATE, publish_target="architecture",
                        candidate_from_work_item_id="architecture-integration",
                    ),
                ),
            )

            result = GraphRunner(
                self.agents, self.tools, max_workers=2, artifacts=repository
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)
            self.assertEqual(
                (Path(project_path) / "architecture.md").read_text(encoding="utf-8"),
                "# Integrated architecture",
            )


class WorkflowTemplateTest(unittest.TestCase):
    def test_runner_rejects_uncompiled_standard_task_execution(self) -> None:
        agents = AgentRegistry()
        gateway = ToolGateway()
        created: dict[str, list[FakeAgent]] = {}

        for agent_id, domain, content in (
            ("requirement_agent", "requirement", "需求文档"),
            ("architecture_agent", "architecture", "架构文档"),
            ("architecture_contract_agent", "architecture_contract", "实现合同"),
            ("task_agent", "task", "任务清单"),
            ("bootstrap_agent", "bootstrap", "环境报告"),
            ("code_agent", "code", "实现摘要"),
            ("test_agent", "test", "测试报告"),
            ("review_agent", "review", "审查报告"),
        ):
            instances: list[FakeAgent] = []

            def factory(
                result: str = content, target: list[FakeAgent] = instances
            ) -> FakeAgent:
                agent = FakeAgent(AgentResult.completed(result))
                target.append(agent)
                return agent

            agents.register(
                AgentDefinition(
                    id=agent_id,
                    domain=domain,
                    description=f"{agent_id} description",
                    output_key={
                        "requirement_agent": "requirement",
                        "architecture_agent": "architecture",
                        "architecture_contract_agent": "architecture_contract",
                        "task_agent": "tasks",
                        "bootstrap_agent": "environment",
                        "code_agent": "implementation",
                        "test_agent": "tests",
                        "review_agent": "review",
                    }[agent_id],
                ),
                factory=factory,
            )
            created[agent_id] = instances

        plan = plan_from_template(project_delivery_template())
        result = GraphRunner(agents, gateway).run(plan)

        self.assertEqual(result.status, GraphRunStatus.FAILED)
        self.assertIn("标准执行方式", result.error)

    def test_project_delivery_template_defines_the_standard_task_pipeline(self) -> None:
        template = project_delivery_template()

        self.assertEqual(
            [node.agent_id for node in template.nodes],
            [
                "requirement_agent",
                "architecture_agent",
                "architecture_contract_agent",
                "task_agent",
                "task_agent",
                "task_agent",
                "bootstrap_agent",
                "code_integration_agent",
                "test_agent",
                "review_agent",
            ],
        )
        self.assertEqual(template.nodes[1].depends_on, ("requirement",))
        self.assertEqual(template.nodes[2].depends_on, ("architecture",))
        self.assertEqual(
            template.nodes[3].depends_on,
            ("requirement", "architecture", "architecture-contract"),
        )
        self.assertEqual(template.nodes[4].depends_on, ("tasks-plan",))
        self.assertEqual(template.nodes[5].depends_on, ("tasks-integration",))
        self.assertEqual(
            template.nodes[6].depends_on,
            ("requirement", "architecture", "architecture-contract", "tasks-quality-gate"),
        )
        self.assertEqual(template.nodes[6].output_key, "environment")
        self.assertEqual(
            template.nodes[7].depends_on,
            ("architecture-contract", "tasks-quality-gate", "environment"),
        )
        self.assertEqual(template.nodes[7].execution_mode, ExecutionMode.INTEGRATION)
        self.assertEqual(template.nodes[7].publish_target, "workspace")

    def test_template_registry_returns_template_experience(self) -> None:
        template = WorkflowTemplate(
            id="planner_hint_test",
            name="Planner 测试模板",
            description="测试模板注册与查询",
            nodes=(
                TaskBlueprint(
                    id="requirement",
                    agent_id="requirement_agent",
                    objective="生成需求",
                    output_key="requirement_result",
                ),
            ),
        )
        registry = WorkflowTemplateRegistry()
        registry.register(template)

        registered = registry.get("planner_hint_test")

        self.assertEqual(registered, template)
        self.assertEqual(registered.nodes[0].agent_id, "requirement_agent")

    def test_template_registry_rejects_duplicate_ids(self) -> None:
        registry = WorkflowTemplateRegistry()
        template = WorkflowTemplate(
            id="planner_hint_test",
            name="Planner 测试模板",
            description="测试模板重复注册",
            nodes=(
                TaskBlueprint(
                    id="requirement",
                    agent_id="requirement_agent",
                    objective="生成需求",
                    output_key="requirement_result",
                ),
            ),
        )
        registry.register(template)

        with self.assertRaisesRegex(ValueError, "已注册"):
            registry.register(template)

    def test_code_agent_without_save_tool_gets_control_plane_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("验证实现摘要强制要求")
            created: list[FakeAgent] = []

            def factory() -> FakeAgent:
                agent = FakeAgent(AgentResult.completed("代码已写入，摘要如下"))
                created.append(agent)
                return agent

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=factory,
            )
            plan = ExecutionPlan(
                id="implementation-summary-required",
                goal="实现代码",
                trace=trace,
                work_items=(make_node("code", agent_id="code_agent"),),
            )

            repository = ArtifactRepository(project_path)
            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=repository,
            ).run(plan)

            # 重试后仍不保存时，控制面把最终回答固化为 implementation.md，而不是硬失败
            self.assertEqual(result.status, GraphRunStatus.COMPLETED)
            self.assertEqual(len(created), 2)
            self.assertTrue(repository.exists("implementation"))
            saved = Path(project_path, "implementation.md").read_text(encoding="utf-8")
            self.assertIn("代码已写入", saved)

    def test_code_agent_saving_implementation_summary_completes(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("实现摘要已保存即可完成")

            class CodeSavingSummary:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    Path(project_path, "implementation.md").write_text(
                        "## 实现范围\n\n已保存实现摘要。", encoding="utf-8"
                    )
                    return AgentResult.completed("实现摘要已保存")

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=CodeSavingSummary,
            )
            plan = ExecutionPlan(
                id="implementation-summary-saved",
                goal="实现代码",
                trace=trace,
                work_items=(make_node("code", agent_id="code_agent"),),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)

    def test_repair_code_item_without_file_writes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("修复未落盘必须被闸门拒绝")
            created: list[FakeAgent] = []

            def factory() -> FakeAgent:
                agent = FakeAgent(AgentResult.completed("已定位缺陷，但本轮未落盘"))
                created.append(agent)
                return agent

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=factory,
            )
            repair_item = make_node("repair-code", agent_id="code_agent")
            plan = ExecutionPlan(
                id="repair-no-file-change",
                goal="修复测试失败",
                trace=trace,
                work_items=(
                    WorkItem(
                        id=repair_item.id,
                        agent_id=repair_item.agent_id,
                        objective=repair_item.objective,
                        output_key=repair_item.output_key,
                        dependencies=repair_item.dependencies,
                        failure_package=FailurePackage(
                            signal=FailureSignal(
                                FailureKind.TEST_FAILURE, "unit check exit_code=1"
                            )
                        ),
                    ),
                ),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
                memory=MemoryStore(project_path),
            ).run(plan)

            # 重试一次仍不落盘时，修复项判定失败而不是假装完成
            self.assertEqual(result.status, GraphRunStatus.FAILED)
            self.assertEqual(len(created), 2)
            self.assertIn("repair_no_file_change", result.error or "")

    def test_repair_gate_ignores_historical_writes_for_reused_work_item(self) -> None:
        """同一 WorkItem 跨修复轮次复用时，旧写入不能满足本轮落盘闸门。"""
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("忽略历史修复写入")
            memory = MemoryStore(project_path)
            memory.append(
                trace_id=trace.trace_id,
                role="tool",
                event_type="tool_result",
                content="已写入 workspace/old.py",
                work_item_id="repair-code",
                agent_id="code_agent",
                tool_name="write_workspace_file",
            )

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=lambda: FakeAgent(AgentResult.completed("本轮未写入")),
            )
            repair_item = make_node("repair-code", agent_id="code_agent")
            plan = ExecutionPlan(
                id="repair-ignore-history",
                goal="修复测试失败",
                trace=trace,
                work_items=(
                    WorkItem(
                        id=repair_item.id,
                        agent_id=repair_item.agent_id,
                        objective=repair_item.objective,
                        output_key=repair_item.output_key,
                        failure_package=FailurePackage(
                            signal=FailureSignal(FailureKind.TEST_FAILURE, "unit failed")
                        ),
                    ),
                ),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
                memory=memory,
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.FAILED)
            self.assertIn("repair_no_file_change", result.error or "")

    def test_repair_code_item_with_file_writes_completes(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("修复落盘即可完成")

            class RepairWritingCode:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    Path(project_path, "implementation.md").write_text(
                        "## 实现范围\n\n已保存实现摘要。", encoding="utf-8"
                    )
                    assert context is not None and context.memory is not None
                    context.memory.append(
                        trace_id=context.trace_id,
                        role="tool",
                        event_type="tool_result",
                        content="已写入 server.py",
                        work_item_id=context.work_item_id,
                        agent_id=context.agent_id,
                        tool_name="write_workspace_file",
                    )
                    return AgentResult.completed("已修复 server.py 并落盘")

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=RepairWritingCode,
            )
            repair_item = make_node("repair-code", agent_id="code_agent")
            plan = ExecutionPlan(
                id="repair-with-writes",
                goal="修复测试失败",
                trace=trace,
                work_items=(
                    WorkItem(
                        id=repair_item.id,
                        agent_id=repair_item.agent_id,
                        objective=repair_item.objective,
                        output_key=repair_item.output_key,
                        dependencies=repair_item.dependencies,
                        failure_package=FailurePackage(
                            signal=FailureSignal(
                                FailureKind.TEST_FAILURE, "unit check exit_code=1"
                            )
                        ),
                    ),
                ),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
                memory=MemoryStore(project_path),
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)

    def test_review_blocked_verdict_marks_run_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("审查结论 BLOCKED 应阻塞交付")

            class CodeSavingSummary:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    Path(project_path, "implementation.md").write_text(
                        "## 实现范围\n\n已保存实现摘要。", encoding="utf-8"
                    )
                    return AgentResult.completed("实现摘要已保存")

            class BlockedReview:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    return AgentResult.completed(
                        "## 审查结论（BLOCKED）\n\n缺少外部规范核实记录，不予放行。"
                    )

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=CodeSavingSummary,
            )
            agents.register(
                AgentDefinition("review_agent", "review", "review", "review"),
                factory=BlockedReview,
            )
            plan = ExecutionPlan(
                id="review-blocked",
                goal="实现并审查",
                trace=trace,
                work_items=(
                    make_node("code", agent_id="code_agent", output_key="implementation"),
                    make_node(
                        "review",
                        agent_id="review_agent",
                        output_key="review",
                        depends_on=("code",),
                    ),
                ),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.BLOCKED)
            self.assertIn("BLOCKED", result.error)

    def test_review_pass_verdict_stays_completed(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            traces = TraceStore(project_path)
            trace = traces.start_trace("审查结论 PASS 保持 completed")

            class CodeSavingSummary:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    Path(project_path, "implementation.md").write_text(
                        "## 实现范围\n\n已保存实现摘要。", encoding="utf-8"
                    )
                    return AgentResult.completed("实现摘要已保存")

            class PassedReview:
                def run(
                    self, task: str, *, context: ExecutionContext | None = None
                ) -> AgentResult:
                    return AgentResult.completed(
                        "## 审查结论（CONDITIONAL_PASS）\n\n条件已记录。"
                    )

            agents = AgentRegistry()
            agents.register(
                AgentDefinition("code_agent", "code", "code", "implementation"),
                factory=CodeSavingSummary,
            )
            agents.register(
                AgentDefinition("review_agent", "review", "review", "review"),
                factory=PassedReview,
            )
            plan = ExecutionPlan(
                id="review-pass",
                goal="实现并审查",
                trace=trace,
                work_items=(
                    make_node("code", agent_id="code_agent", output_key="implementation"),
                    make_node(
                        "review",
                        agent_id="review_agent",
                        output_key="review",
                        depends_on=("code",),
                    ),
                ),
            )

            result = GraphRunner(
                agents,
                ToolGateway(),
                traces=traces,
                artifacts=ArtifactRepository(project_path),
            ).run(plan)

            self.assertEqual(result.status, GraphRunStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()

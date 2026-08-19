import unittest
from threading import Barrier
import tempfile
from pathlib import Path

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.memory.store import MemoryStore
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.trace import TraceContext, TraceStore
from app.workflow.template import (
    TaskBlueprint,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
)
from app.workflow.templates import project_delivery_template
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


class GraphRunnerTest(unittest.TestCase):
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
        self.assertEqual(result.node_result.node_id, "requirement")

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
        self.assertEqual(result.node_result.node_id, "requirement")

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

    def test_architecture_scopes_integrate_then_quality_gate_promotes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            repository = ArtifactRepository(project_path)
            workflow = ArchitectureArtifactWorkflow(project_path)
            trace = TraceContext(requirement_id="req-architecture", trace_id="tr-architecture")

            class ArchitectureWorkflowAgent:
                def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
                    assert context is not None
                    if context.execution_mode is ExecutionMode.PARTITIONED:
                        workflow.write_staged(context, f"# {context.output_slot}")
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
                        execution_mode=ExecutionMode.PARTITIONED, output_slot="api",
                    ),
                    WorkItem(
                        id="architecture-data", agent_id="architecture_agent", objective="设计数据层",
                        output_key="architecture_data", artifact_key="architecture",
                        execution_mode=ExecutionMode.PARTITIONED, output_slot="data",
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
                "task_agent",
                "task_agent",
                "task_agent",
                "bootstrap_agent",
                "code_agent",
                "test_agent",
                "review_agent",
            ],
        )
        self.assertEqual(template.nodes[1].depends_on, ("requirement",))
        self.assertEqual(
            template.nodes[2].depends_on, ("requirement", "architecture")
        )
        self.assertEqual(template.nodes[3].depends_on, ("tasks-plan",))
        self.assertEqual(template.nodes[4].depends_on, ("tasks-integration",))
        self.assertEqual(
            template.nodes[5].depends_on,
            ("requirement", "architecture", "tasks-quality-gate"),
        )
        self.assertEqual(template.nodes[5].output_key, "environment")
        self.assertEqual(
            template.nodes[6].depends_on,
            ("requirement", "architecture", "tasks-quality-gate", "environment"),
        )

    def test_template_registry_returns_template_experience(self) -> None:
        template = WorkflowTemplate(
            id="requirement_generation",
            name="需求生成",
            description="生成需求文档",
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

        registered = registry.get("requirement_generation")

        self.assertEqual(registered, template)
        self.assertEqual(registered.nodes[0].agent_id, "requirement_agent")

    def test_template_registry_rejects_duplicate_ids(self) -> None:
        registry = WorkflowTemplateRegistry()
        template = WorkflowTemplate(
            id="requirement_generation",
            name="需求生成",
            description="生成需求文档",
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


if __name__ == "__main__":
    unittest.main()

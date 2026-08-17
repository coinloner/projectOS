import unittest

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.execution_context import ExecutionContext
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


class WorkflowTemplateTest(unittest.TestCase):
    def test_runner_executes_the_full_project_delivery_chain(self) -> None:
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

        self.assertEqual(result.status, GraphRunStatus.COMPLETED)
        self.assertEqual(
            result.state.artifacts,
            {
                "requirement": "需求文档",
                "architecture": "架构文档",
                "tasks": "任务清单",
                "environment": "环境报告",
                "implementation": "实现摘要",
                "tests": "测试报告",
                "review": "审查报告",
            },
        )
        task_prompt = created["task_agent"][0].tasks[0]
        self.assertIn("- requirement", task_prompt)
        self.assertIn("- architecture", task_prompt)
        implementation_prompt = created["code_agent"][0].tasks[0]
        self.assertIn("- environment", implementation_prompt)
        self.assertIn("- tasks", implementation_prompt)
        test_prompt = created["test_agent"][0].tasks[0]
        self.assertIn("- implementation", test_prompt)
        review_prompt = created["review_agent"][0].tasks[0]
        self.assertIn("- tests", review_prompt)

    def test_project_delivery_template_defines_a_seven_agent_dag(self) -> None:
        template = project_delivery_template()

        self.assertEqual(
            [node.agent_id for node in template.nodes],
            [
                "requirement_agent",
                "architecture_agent",
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
        self.assertEqual(
            template.nodes[3].depends_on,
            ("requirement", "architecture", "tasks"),
        )
        self.assertEqual(template.nodes[3].output_key, "environment")
        self.assertEqual(
            template.nodes[4].depends_on,
            ("requirement", "architecture", "tasks", "environment"),
        )
        self.assertEqual(
            template.nodes[5].depends_on,
            ("requirement", "tasks", "environment", "implementation"),
        )
        self.assertEqual(
            template.nodes[6].depends_on,
            ("requirement", "architecture", "tasks", "environment", "implementation", "tests"),
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

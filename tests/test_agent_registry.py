import unittest

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult


class FakeAgent:
    def __init__(self, label: str) -> None:
        self.label = label

    def run(self, task: str) -> AgentResult:
        return AgentResult.completed(f"{self.label}: {task}")


class AgentRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = AgentDefinition(
            id="requirement_agent",
            domain="requirement",
            description="将用户描述整理为结构化需求草稿",
            output_key="requirement",
        )
        self.registry = AgentRegistry()

    def test_registry_exposes_definition_but_not_factory(self) -> None:
        self.registry.register(
            self.definition,
            factory=lambda: FakeAgent("requirement"),
        )

        self.assertEqual(self.registry.definition("requirement_agent"), self.definition)
        self.assertEqual(self.registry.definitions(), (self.definition,))
        self.assertFalse(hasattr(self.definition, "factory"))

    def test_registry_creates_a_fresh_agent_instance(self) -> None:
        created: list[FakeAgent] = []

        def build_agent() -> FakeAgent:
            agent = FakeAgent(f"agent-{len(created)}")
            created.append(agent)
            return agent

        self.registry.register(self.definition, factory=build_agent)

        first = self.registry.create("requirement_agent")
        second = self.registry.create("requirement_agent")

        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)
        self.assertEqual(first.run("草稿").content, "agent-0: 草稿")

    def test_registry_rejects_duplicate_and_unknown_agents(self) -> None:
        self.registry.register(self.definition, factory=lambda: FakeAgent("first"))

        with self.assertRaisesRegex(ValueError, "已注册"):
            self.registry.register(
                self.definition,
                factory=lambda: FakeAgent("duplicate"),
            )

        with self.assertRaisesRegex(KeyError, "未注册 Agent"):
            self.registry.create("missing_agent")

    def test_registry_rejects_duplicate_output_key(self) -> None:
        self.registry.register(self.definition, factory=lambda: FakeAgent("first"))

        with self.assertRaisesRegex(ValueError, "output_key 'requirement' 已被"):
            self.registry.register(
                AgentDefinition(
                    id="another_requirement_agent",
                    domain="review",
                    description="复核需求",
                    output_key="requirement",
                ),
                factory=lambda: FakeAgent("second"),
            )

    def test_definition_requires_complete_planner_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "AgentDefinition.domain 不能为空"):
            AgentDefinition(
                id="requirement_agent",
                domain="",
                description="需求",
                output_key="requirement",
            )

        with self.assertRaisesRegex(ValueError, "AgentDefinition.output_key 不能为空"):
            AgentDefinition(
                id="requirement_agent",
                domain="requirement",
                description="需求",
                output_key="",
            )


if __name__ == "__main__":
    unittest.main()

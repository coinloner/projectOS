import unittest
from unittest.mock import patch

from app.agent.base_agent import BaseAgent
from app.agent.result import AgentStatus
from app.tool_manager.gateway import ToolGateway


class FakeCrewAgent:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.tasks: list[object] = []

    def execute_task(self, task: object) -> str:
        self.tasks.append(task)
        return '{"type": "capability_request", "capability": "external_research", "reason": "需要规范"}'


class CrewAIAgentAdapterTest(unittest.TestCase):
    def test_base_agent_delegates_tool_runtime_to_crewai(self) -> None:
        gateway = ToolGateway()
        created: list[FakeCrewAgent] = []

        def build_agent(**kwargs):
            agent = FakeCrewAgent(**kwargs)
            created.append(agent)
            return agent

        with patch("app.agent.base_agent.Agent", side_effect=build_agent), patch(
            "app.agent.base_agent.Task", side_effect=lambda **kwargs: kwargs
        ), patch("app.agent.base_agent.build_llm", return_value=object()):
            agent = BaseAgent(
                gateway=gateway,
                domain="requirement",
                role="需求分析师",
                goal="生成需求文档",
                backstory="整理用户需求",
            )
            result = agent.run("生成一个项目需求")

        self.assertEqual(result.status, AgentStatus.NEEDS_CAPABILITY)
        self.assertEqual(created[0].kwargs["tools"], [])
        self.assertEqual(created[0].kwargs["max_iter"], 10)
        self.assertEqual(created[0].tasks[0]["description"], "生成一个项目需求")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest

from app.artifact.store import ArtifactStore
from app.agent.registry import AgentDefinition, AgentRegistry
from app.planner.evaluation import PlannerEvaluator, PlannerScenario
from app.planner.service import PlannerService
from app.workflow.template import WorkflowTemplateRegistry


def build_agents() -> AgentRegistry:
    registry = AgentRegistry()
    for agent_id, domain, output_key in (
        ("requirement_agent", "requirement", "requirement"),
        ("architecture_agent", "architecture", "architecture"),
        ("code_agent", "code", "implementation"),
    ):
        registry.register(
            AgentDefinition(id=agent_id, domain=domain, description=agent_id, output_key=output_key),
            factory=lambda: None,
        )
    return registry


def build_templates() -> WorkflowTemplateRegistry:
    return WorkflowTemplateRegistry()


class ScenarioRuntime:
    def __init__(self, response: str) -> None:
        self.response = response

    def generate(self, prompt: str) -> str:
        return self.response


class PlannerEvaluationTest(unittest.TestCase):
    def test_report_contains_path_and_quality_metrics(self) -> None:
        response = (
            '{"rationale":"完成架构","template_hint_id":null,"steps":['
            '{"ref":"architecture","agent_id":"architecture_agent","objective":"设计架构"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            planner = PlannerService(
                runtime=ScenarioRuntime(response),
                agents=build_agents(),
                templates=build_templates(),
                artifacts=ArtifactStore(directory),
            )
            report = PlannerEvaluator().evaluate(
                planner,
                [
                    PlannerScenario(
                        name="architecture",
                        goal="已有需求，请做技术架构",
                        required_agents=("architecture_agent",),
                        expected_order=("architecture_agent",),
                    )
                ],
                repetitions=2,
            )

        self.assertEqual(report.success_count, 1)
        self.assertEqual(report.results[0].selected_agents, ("architecture_agent",))
        self.assertTrue(report.results[0].stable)
        self.assertEqual(report.as_dict()["total"], 1)

    def test_report_marks_forbidden_agent(self) -> None:
        response = (
            '{"rationale":"错误路径","template_hint_id":null,"steps":['
            '{"ref":"architecture","agent_id":"architecture_agent","objective":"写架构"}]}'
        )
        with tempfile.TemporaryDirectory() as directory:
            planner = PlannerService(
                runtime=ScenarioRuntime(response),
                agents=build_agents(),
                templates=build_templates(),
                artifacts=ArtifactStore(directory),
            )
            report = PlannerEvaluator().evaluate(
                planner,
                [PlannerScenario(name="requirement", goal="整理想法", forbidden_agents=("architecture_agent",))],
            )

        self.assertFalse(report.results[0].success)
        self.assertEqual(report.results[0].forbidden_agents, ("architecture_agent",))


if __name__ == "__main__":
    unittest.main()

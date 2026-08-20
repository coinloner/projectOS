import unittest
from unittest.mock import patch

from app.planner.planner import CrewAIPlannerRuntime


class PlannerRuntimeTest(unittest.TestCase):
    def test_runtime_reuses_llm_and_optional_prompt_cache(self) -> None:
        fake_agent = type("FakeAgent", (), {"execute_task": lambda self, task: '{"steps": []}'})()
        with patch("app.planner.planner.build_llm", return_value=object()) as build_llm, patch(
            "app.planner.planner.Agent", return_value=fake_agent
        ) as agent, patch("app.planner.planner.Task"):
            runtime = CrewAIPlannerRuntime(cache_enabled=True)
            self.assertEqual(runtime.generate("same prompt"), '{"steps": []}')
            self.assertEqual(runtime.generate("same prompt"), '{"steps": []}')

        build_llm.assert_called_once_with(temperature=0.0, seed=0)
        agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()

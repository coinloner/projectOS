import unittest
from unittest.mock import patch

from app.planner.planner import CrewAIPlannerRuntime
from app.llm.config import LLMSelection


class PlannerRuntimeTest(unittest.TestCase):
    def test_runtime_reuses_llm_and_optional_prompt_cache(self) -> None:
        fake_agent = type("FakeAgent", (), {"execute_task": lambda self, task: '{"steps": []}'})()
        with patch("app.planner.planner.build_llm", return_value=object()) as build_llm, patch(
            "app.planner.planner.Agent", return_value=fake_agent
        ) as agent, patch("app.planner.planner.Task"):
            runtime = CrewAIPlannerRuntime(cache_enabled=True)
            self.assertEqual(runtime.generate("same prompt"), '{"steps": []}')
            self.assertEqual(runtime.generate("same prompt"), '{"steps": []}')

        build_llm.assert_called_once_with(temperature=0.0)
        agent.assert_called_once()

    def test_responses_selection_calls_text_adapter_without_crewai_agent_loop(self) -> None:
        fake_llm = type("FakeResponsesLLM", (), {"call": lambda self, prompt: '{"steps": []}'})()
        selection = LLMSelection(
            provider="portdan",
            model="gpt-5.5",
            base_url="https://portdan.com",
            api_key_env="PORTDAN_API_KEY",
            crewai_provider="openai",
            wire_api="responses",
        )
        with patch("app.planner.planner.build_llm", return_value=fake_llm), patch(
            "app.planner.planner.OpenAIResponsesLLM", fake_llm.__class__
        ), patch("app.planner.planner.Agent") as agent:
            runtime = CrewAIPlannerRuntime(llm_selection=selection)
            self.assertEqual(runtime.generate("prompt"), '{"steps": []}')
        agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()

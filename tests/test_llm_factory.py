import os
import unittest
from unittest.mock import patch

from app.llm.factory import build_llm


class LLMFactoryTest(unittest.TestCase):
    def test_builds_the_configured_crewai_provider(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "deepseek-v4-pro")
        self.assertEqual(llm.provider, "deepseek")
        self.assertEqual(llm.base_url, "https://api.deepseek.com")

    def test_missing_api_key_fails_before_crewai_executes(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                build_llm()

    def test_environment_selects_provider_and_overrides_model(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "PROJECTOS_LLM_PROVIDER": "openai",
                "PROJECTOS_LLM_MODEL": "gpt-4.1-mini",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.model, "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()

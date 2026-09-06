import os
import unittest
from unittest.mock import patch

from app.llm.factory import build_llm
from app.llm.config import resolve_llm_selection
from app.llm.config import discover_provider_models
from app.llm.responses import OpenAIResponsesLLM


class LLMFactoryTest(unittest.TestCase):
    def test_provider_model_discovery_reads_remote_ids_without_exposing_key(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"data":[{"id":"zai-org/GLM-5.2"},{"id":"Qwen/Qwen3-8B"}]}'

        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-key"}, clear=True), patch(
            "app.llm.config.urlopen", return_value=Response()
        ) as urlopen:
            models = discover_provider_models("siliconflow")

        self.assertEqual(models, ("Qwen/Qwen3-8B", "zai-org/GLM-5.2"))
        request = urlopen.call_args.args[0]
        self.assertNotIn("test-key", str(request))
    def test_builds_the_configured_crewai_provider(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {"PROJECTOS_LLM_PROVIDER": "fhl", "FHL_API_KEY": "test-key"},
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "gpt-5.6-terra")
        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.base_url, "https://www.fhl.mom/v1")
        self.assertTrue(llm.stream)

    def test_missing_api_key_fails_before_crewai_executes(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ, {"PROJECTOS_LLM_PROVIDER": "fhl"}, clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "FHL_API_KEY"):
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

    def test_siliconflow_uses_openai_compatible_endpoint(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "siliconflow",
                "SILICONFLOW_API_KEY": "test-key",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "deepseek-ai/DeepSeek-V4-Pro")
        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.base_url, "https://api.siliconflow.cn/v1")

    def test_model_can_be_switched_without_code_changes(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "siliconflow",
                "PROJECTOS_LLM_MODEL": "zai-org/GLM-5.2",
                "SILICONFLOW_API_KEY": "test-key",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "zai-org/GLM-5.2")

    def test_provider_selects_only_its_key_when_multiple_keys_are_configured(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "siliconflow",
                "SILICONFLOW_API_KEY": "siliconflow-key",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "OPENAI_API_KEY": "openai-key",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.api_key, "siliconflow-key")

    def test_siliconflow_missing_api_key_reports_its_environment_name(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {"PROJECTOS_LLM_PROVIDER": "siliconflow"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "SILICONFLOW_API_KEY"):
                build_llm()

    def test_fhl_uses_openai_compatible_endpoint_and_model(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {"PROJECTOS_LLM_PROVIDER": "fhl", "FHL_API_KEY": "test-key"},
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "gpt-5.6-terra")
        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.base_url, "https://www.fhl.mom/v1")


    def test_totoken_uses_openai_responses_endpoint_and_actor_header(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "totoken",
                "TOTOKEN_API_KEY": "test-key",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "gpt-5.6-sol")
        self.assertEqual(llm.provider, "openai")
        self.assertEqual(llm.base_url, "https://totokens.cc")
        self.assertIsInstance(llm, OpenAIResponsesLLM)
        self.assertEqual(
            llm.default_headers,
            {"x-openai-actor-authorization": "local-image-extension"},
        )
        self.assertTrue(llm.stream)

    def test_totoken_model_discovery_does_not_invent_catalog_route(self) -> None:
        with patch.dict(
            os.environ,
            {"TOTOKEN_API_KEY": "test-key"},
            clear=True,
        ), patch("app.llm.config.urlopen") as urlopen:
            self.assertEqual(discover_provider_models("totoken"), ("gpt-5.6-sol",))
        urlopen.assert_not_called()

    def test_portdan_defaults_to_streaming(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {"PROJECTOS_LLM_PROVIDER": "portdan", "PORTDAN_API_KEY": "test-key"},
            clear=True,
        ):
            llm = build_llm()

        self.assertEqual(llm.model, "gpt-5.5")
        self.assertEqual(llm.base_url, "https://portdan.com")
        self.assertTrue(llm.stream)

    def test_restored_responses_selection_builds_responses_llm(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {"PORTDAN_API_KEY": "test-key"},
            clear=True,
        ):
            selection = resolve_llm_selection("portdan")
            llm = build_llm(selection=selection)

        self.assertEqual(selection.wire_api, "responses")
        self.assertIsInstance(llm, OpenAIResponsesLLM)
        self.assertEqual(llm.base_url, "https://portdan.com")

    def test_stream_environment_override_can_disable_portdan_sse(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "portdan",
                "PORTDAN_API_KEY": "test-key",
                "PROJECTOS_LLM_STREAM": "false",
            },
            clear=True,
        ):
            llm = build_llm()

        self.assertFalse(llm.stream)

    def test_explicit_selection_overrides_deployment_defaults(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "deepseek",
                "PROJECTOS_LLM_MODEL": "stale-default",
                "SILICONFLOW_API_KEY": "test-key",
            },
            clear=True,
        ):
            selection = resolve_llm_selection(
                "siliconflow", model="zai-org/GLM-5.2"
            )
            llm = build_llm(selection=selection)

        self.assertEqual(selection.provider, "siliconflow")
        self.assertEqual(llm.model, "zai-org/GLM-5.2")
        self.assertEqual(llm.api_key, "test-key")

    def test_llm_transport_guards_are_configurable(self) -> None:
        with patch("app.llm.factory.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROJECTOS_LLM_PROVIDER": "fhl",
                "FHL_API_KEY": "test-key",
                "PROJECTOS_LLM_MAX_TOKENS": "2048",
                "PROJECTOS_LLM_TIMEOUT_SECONDS": "90",
            },
            clear=True,
        ):
            llm = build_llm()
        self.assertEqual(llm.max_tokens, 2048)
        self.assertEqual(llm.timeout, 90.0)


if __name__ == "__main__":
    unittest.main()

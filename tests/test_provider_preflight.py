import os
import unittest
from unittest.mock import patch

import httpx

from app.llm.config import LLMSelection
from app.llm.preflight import ProviderPreflight, ProviderPreflightError


class _FakeResponse:
    def __init__(self, status_code: int, lines=(), body: bytes = b"") -> None:
        self.status_code = status_code
        self._lines = tuple(lines)
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url, **kwargs):
        self.request = (method, url, kwargs)
        return self.response


class ProviderPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = LLMSelection(
            provider="test",
            model="test-model",
            base_url="https://provider.test/v1",
            api_key_env="TEST_PREFLIGHT_KEY",
            crewai_provider="openai",
            wire_api="responses",
        )

    def test_passes_only_after_terminal_event_and_sends_tool_schema(self) -> None:
        client = _FakeClient(_FakeResponse(200, lines=(
            "event: response.output_text.delta",
            "data: OK",
            "event: response.completed",
            "data: {}",
        )))
        with patch.dict(os.environ, {"TEST_PREFLIGHT_KEY": "secret"}, clear=False):
            result = ProviderPreflight(client_factory=lambda **_kwargs: client).check(self.selection)
        self.assertTrue(result.passed)
        self.assertTrue(result.terminal_seen)
        self.assertTrue(result.tool_schema_accepted)
        method, url, kwargs = client.request
        self.assertEqual((method, url), ("POST", "https://provider.test/v1/responses"))
        self.assertEqual(kwargs["json"]["model"], "test-model")
        self.assertEqual(kwargs["json"]["tools"][0]["type"], "function")
        # The real Wanfa Responses endpoint rejects values below 16.
        self.assertGreaterEqual(kwargs["json"]["max_output_tokens"], 16)

    def test_policy_rejection_is_non_retryable(self) -> None:
        client = _FakeClient(_FakeResponse(
            403,
            body=b"{'error': {'code': 'upstream_policy_rejected'}}",
        ))
        with patch.dict(os.environ, {"TEST_PREFLIGHT_KEY": "secret"}, clear=False):
            with self.assertRaises(ProviderPreflightError) as raised:
                ProviderPreflight(client_factory=lambda **_kwargs: client).check(self.selection)
        self.assertEqual(raised.exception.result.status, "blocked")
        self.assertFalse(raised.exception.result.retryable)
        self.assertEqual(raised.exception.result.http_status, 403)

    def test_missing_api_key_fails_before_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderPreflightError) as raised:
                ProviderPreflight(client_factory=lambda **_kwargs: self.fail("network called")).check(self.selection)
        self.assertEqual(raised.exception.result.status, "unavailable")
        self.assertFalse(raised.exception.result.retryable)

    def test_missing_terminal_event_is_retryable_transport_signal(self) -> None:
        client = _FakeClient(_FakeResponse(200, lines=("data: delta",)))
        with patch.dict(os.environ, {"TEST_PREFLIGHT_KEY": "secret"}, clear=False):
            with self.assertRaises(ProviderPreflightError) as raised:
                ProviderPreflight(client_factory=lambda **_kwargs: client).check(self.selection)
        self.assertEqual(raised.exception.result.status, "failed")
        self.assertTrue(raised.exception.result.retryable)


if __name__ == "__main__":
    unittest.main()

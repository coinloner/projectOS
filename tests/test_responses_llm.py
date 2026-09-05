import os
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm.responses import OpenAIResponsesLLM


class _FakeResponses:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def create(self, **params):
        self.calls.append(params)
        return iter(self.events)


class _RaisingResponses:
    def __init__(self, error):
        self.error = error

    def create(self, **params):
        def stream():
            yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-error"))
            raise self.error

        return stream()


class _FakeClient:
    def __init__(self, events):
        self.responses = _FakeResponses(events)


class _FakeStream:
    def __init__(self, events, *, status_code=200, headers=None):
        self._events = events
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers if headers is not None else {},
        )

    def __iter__(self):
        return iter(self._events)


class _FakeAsyncResponses:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)

        async def stream():
            for event in self.events:
                yield event

        return stream()


class _FakeAsyncClient:
    def __init__(self, events):
        self.responses = _FakeAsyncResponses(events)


def _llm(stream=True):
    return OpenAIResponsesLLM(
        model="gpt-5.5",
        api_key="test-key",
        base_url="https://portdan.com",
        provider="openai",
        stream=stream,
        max_tokens=256,
        seed=7,
    )


class ResponsesLLMTest(unittest.TestCase):
    def test_streaming_text_uses_responses_wire_and_filters_seed(self):
        events = [
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="r1")),
            SimpleNamespace(type="response.output_text.delta", delta="hello"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r1", status="completed", usage=None)),
        ]
        client = _FakeClient(events)
        llm = _llm()
        with patch.object(llm, "_get_sync_client", return_value=client):
            self.assertEqual(llm.call("say hi"), "hello")
        params = client.responses.calls[0]
        self.assertTrue(params["stream"])
        self.assertEqual(params["max_output_tokens"], 256)
        self.assertNotIn("seed", params)
        self.assertEqual(params["input"], [{"role": "user", "content": "say hi"}])

    def test_streaming_function_call_is_executed_via_available_functions(self):
        item = SimpleNamespace(
            type="function_call",
            call_id="call-1",
            name="lookup",
            arguments='{"value": 3}',
        )
        events = [
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="r2")),
            SimpleNamespace(type="response.output_item.done", item=item),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r2", status="completed", usage=None)),
        ]
        llm = _llm()
        with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
            result = llm.call(
                "look up",
                tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
                available_functions={"lookup": lambda value: f"found:{value}"},
            )
        self.assertEqual(result, "found:3")
        self.assertTrue(llm.supports_function_calling())

    def test_streaming_native_function_call_returns_call_list_to_crewai(self):
        item = SimpleNamespace(
            type="function_call",
            call_id="call-native-1",
            name="lookup",
            arguments='{"value": 4}',
        )
        events = [
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-native")),
            SimpleNamespace(type="response.output_item.done", item=item),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r-native", status="completed", usage=None)),
        ]
        llm = _llm()
        with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
            result = llm.call(
                "look up",
                tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            )
        self.assertEqual(result, [{"id": "call-native-1", "name": "lookup", "arguments": '{"value": 4}'}])

    def test_function_argument_deltas_are_aggregated_and_diagnosed(self):
        events = [
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-delta")),
            SimpleNamespace(type="response.output_item.added", item=SimpleNamespace(type="function_call", call_id="c1", name="lookup")),
            SimpleNamespace(type="response.function_call_arguments.delta", call_id="c1", delta='{"value":'),
            SimpleNamespace(type="response.function_call_arguments.delta", call_id="c1", delta=" 6}"),
            SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(type="function_call", call_id="c1", name="lookup", arguments="")),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r-delta", status="completed", usage=None)),
        ]
        llm = _llm()
        with self.assertLogs("app.llm.responses", level="INFO") as logs:
            with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
                result = llm.call("look up")
        self.assertEqual(result, [{"id": "c1", "name": "lookup", "arguments": '{"value": 6}'}])
        joined = "\n".join(logs.output)
        self.assertIn("responses_sse_start", joined)
        self.assertIn("response.function_call_arguments.delta", joined)
        self.assertIn("argument_chars=12", joined)

    def test_unterminated_stream_is_logged_as_warning(self):
        events = [SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-open"))]
        llm = _llm()
        with self.assertLogs("app.llm.responses", level="WARNING") as logs:
            with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
                with self.assertRaisesRegex(RuntimeError, "without a terminal"):
                    llm.call("hello")
        self.assertIn("responses_sse_end", "\n".join(logs.output))
        self.assertIn("terminal=False", "\n".join(logs.output))

    def test_non_streaming_is_rejected_before_transport(self):
        with self.assertRaisesRegex(RuntimeError, "requires streaming"):
            _llm(stream=False).call("hello")

    def test_empty_native_result_is_rejected(self):
        events = [
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r3", status="completed", usage=None)),
        ]
        llm = _llm()
        with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
            with self.assertRaisesRegex(RuntimeError, "without a terminal"):
                llm.call("hello")

    def test_sse_diagnostics_jsonl_captures_terminal_summary(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="ok"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r-file", status="completed", usage=None)),
        ]
        llm = _llm()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            with patch.dict(os.environ, {"PROJECTOS_SSE_DIAGNOSTICS_PATH": path}):
                with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
                    self.assertEqual(llm.call("hello"), "ok")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
            self.assertEqual(records[0]["kind"], "start")
            self.assertEqual(records[-1]["kind"], "end")
            self.assertTrue(records[-1]["terminal"])
            self.assertEqual(records[-1]["delta_bytes"], 2)

    def test_sse_diagnostics_records_transport_content_type(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="ok"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(id="r-transport", status="completed", usage=None),
            ),
        ]
        llm = _llm()
        client = _FakeClient(events)
        client.responses.create = lambda **params: _FakeStream(
            events,
            headers={"content-type": "text/event-stream; charset=utf-8"},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            with patch.dict(os.environ, {"PROJECTOS_SSE_DIAGNOSTICS_PATH": path}):
                with patch.object(llm, "_get_sync_client", return_value=client):
                    self.assertEqual(llm.call("hello"), "ok")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
        transport = next(record for record in records if record["kind"] == "transport")
        self.assertEqual(transport["http_status"], 200)
        self.assertEqual(transport["content_type"], "text/event-stream; charset=utf-8")

    def test_sse_diagnostics_close_on_stream_exception(self):
        llm = _llm()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            with patch.dict(os.environ, {"PROJECTOS_SSE_DIAGNOSTICS_PATH": path}):
                with patch.object(llm, "_get_sync_client", return_value=SimpleNamespace(
                    responses=_RaisingResponses(RuntimeError("connection reset"))
                )):
                    with self.assertRaisesRegex(RuntimeError, "connection reset"):
                        llm.call("hello")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
            self.assertEqual(records[-2]["kind"], "transport_error")
            self.assertEqual(records[-1]["kind"], "end")
            self.assertFalse(records[-1]["terminal"])

    def test_provider_builtin_tools_are_disabled_by_default(self):
        llm = _llm()
        llm.builtin_tools = ["web_search"]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROJECTOS_ALLOW_PROVIDER_BUILTINS", None)
            with self.assertRaisesRegex(RuntimeError, "disabled by default"):
                llm.call("search")

    def test_provider_builtin_tools_require_explicit_opt_in(self):
        llm = _llm()
        llm.builtin_tools = ["web_search"]
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="ok"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r4", status="completed", usage=None)),
        ]
        client = _FakeClient(events)
        with patch.dict("os.environ", {"PROJECTOS_ALLOW_PROVIDER_BUILTINS": "true"}):
            with patch.object(llm, "_get_sync_client", return_value=client):
                self.assertEqual(llm.call("search"), "ok")
        self.assertEqual(client.responses.calls[0]["tools"], [{"type": "web_search_preview"}])


class AsyncResponsesLLMTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_streaming_native_function_call_returns_call_list_to_crewai(self):
        item = SimpleNamespace(
            type="function_call",
            call_id="call-async-native-1",
            name="lookup",
            arguments='{"value": 5}',
        )
        events = [
            SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-async-native")),
            SimpleNamespace(type="response.output_item.done", item=item),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r-async-native", status="completed", usage=None)),
        ]
        llm = _llm()
        client = _FakeAsyncClient(events)
        with patch.object(llm, "_get_async_client", return_value=client):
            result = await llm.acall(
                "look up",
                tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            )
        self.assertEqual(
            result,
            [{"id": "call-async-native-1", "name": "lookup", "arguments": '{"value": 5}'}],
        )

    async def test_async_non_streaming_is_rejected_before_transport(self):
        with self.assertRaisesRegex(RuntimeError, "requires streaming"):
            await _llm(stream=False).acall("hello")

    async def test_async_sse_diagnostics_jsonl_captures_terminal_summary(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="好"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="r-async-file", status="completed", usage=None)),
        ]
        llm = _llm()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            with patch.dict(os.environ, {"PROJECTOS_SSE_DIAGNOSTICS_PATH": path}):
                with patch.object(llm, "_get_async_client", return_value=_FakeAsyncClient(events)):
                    self.assertEqual(await llm.acall("hello"), "好")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
            self.assertEqual(records[-1]["kind"], "end")
            self.assertTrue(records[-1]["terminal"])
            self.assertEqual(records[-1]["delta_bytes"], len("好".encode("utf-8")))


if __name__ == "__main__":
    unittest.main()

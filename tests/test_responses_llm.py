import os
import json
import tempfile
import unittest
import asyncio
import time
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


class _BlockingStream:
    def __init__(self):
        self.closed = False

    def __iter__(self):
        yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-blocking"))
        while not self.closed:
            time.sleep(10)

    def close(self):
        self.closed = True


class _BlockingResponses:
    def __init__(self, stream):
        self.stream = stream

    def create(self, **params):
        return self.stream


class _BlockingCreateResponses:
    def __init__(self):
        self.release = __import__("threading").Event()
        self.late_stream = _BlockingStream()

    def create(self, **params):
        self.release.wait(timeout=1)
        return self.late_stream


class _BlockingAsyncCreateResponses:
    async def create(self, **params):
        await asyncio.sleep(1)
        return _BlockingAsyncStream()


class _BlockingAsyncStream:
    def __init__(self):
        self.sent_first = False
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.sent_first:
            self.sent_first = True
            return SimpleNamespace(type="response.created", response=SimpleNamespace(id="r-async-blocking"))
        await asyncio.sleep(10)
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class _BlockingAsyncResponses:
    def __init__(self, stream):
        self.stream = stream

    async def create(self, **params):
        return self.stream


class _BlockingAsyncClient:
    def __init__(self, stream):
        self.responses = _BlockingAsyncResponses(stream)


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
    def test_stream_deadline_turns_nonterminal_relay_into_transport_failure(self):
        llm = _llm()
        stream = _BlockingStream()
        with patch.dict(os.environ, {
            "PROJECTOS_RESPONSES_STREAM_MAX_SECONDS": "0.01",
            "PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "1",
        }):
            with patch.object(llm, "_get_sync_client", return_value=SimpleNamespace(
                responses=_BlockingResponses(stream)
            )):
                with self.assertRaisesRegex(TimeoutError, "stream exceeded request ceiling"):
                    llm.call("stall")

    def test_responses_client_bounds_sse_read_and_disables_hidden_retries(self):
        llm = _llm()
        with patch.dict(os.environ, {"PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "17"}, clear=False):
            params = llm._get_client_params()
        self.assertEqual(params["timeout"].read, 17)
        self.assertEqual(params["max_retries"], 0)

    def test_blocking_sync_sse_fails_fast_and_writes_transport_diagnostic(self):
        llm = _llm()
        stream = _BlockingStream()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            env = {
                "PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "0.01",
                "PROJECTOS_SSE_DIAGNOSTICS_PATH": path,
            }
            with patch.dict(os.environ, env):
                with patch.object(llm, "_get_sync_client", return_value=SimpleNamespace(
                    responses=_BlockingResponses(stream)
                )):
                    with self.assertRaisesRegex(TimeoutError, "0.01s without an event"):
                        llm.call("stall")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
        transport = next(record for record in records if record["kind"] == "transport_error")
        self.assertEqual(transport["error_type"], "TimeoutError")
        self.assertEqual(transport["mode"], "sync")
        self.assertFalse(records[-1]["terminal"])

    def test_lazy_client_initialization_is_inside_header_deadline(self):
        llm = _llm()
        release = __import__("threading").Event()

        def blocked_client():
            release.wait(timeout=1)
            return _FakeClient([])

        try:
            with patch.dict(os.environ, {"PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "0.01"}):
                with patch.object(llm, "_get_sync_client", side_effect=blocked_client):
                    with self.assertRaisesRegex(TimeoutError, "SSE create exceeded 0.01s"):
                        llm.call("lazy client stall")
        finally:
            release.set()

    def test_blocking_response_headers_are_bounded_and_late_stream_is_closed(self):
        llm = _llm()
        responses = _BlockingCreateResponses()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            with patch.dict(os.environ, {
                "PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "0.01",
                "PROJECTOS_SSE_DIAGNOSTICS_PATH": path,
            }):
                with patch.object(llm, "_get_sync_client", return_value=SimpleNamespace(responses=responses)):
                    with self.assertRaisesRegex(TimeoutError, "SSE create exceeded 0.01s"):
                        llm.call("headers stall")
                responses.release.set()
                for _ in range(100):
                    if responses.late_stream.closed:
                        break
                    time.sleep(0.001)
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
        self.assertTrue(responses.late_stream.closed)
        self.assertTrue(any(record["kind"] == "transport_error" and record.get("stage") == "create"
                            for record in records))
        self.assertFalse(records[-1]["terminal"])

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

    def test_flat_architecture_writer_call_is_wrapped_without_bypassing_tool(self):
        raw = '{"schema_version":1,"depth":1,"design_id":"module-tasks","module_id":"tasks"}'
        item = SimpleNamespace(
            type="function_call", call_id="call-flat", name="write_module_design",
            arguments=raw,
        )
        events = [
            SimpleNamespace(type="response.output_item.done", item=item),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(
                id="r-flat", status="completed", usage=None)),
        ]
        llm = _llm()
        with patch.object(llm, "_get_sync_client", return_value=_FakeClient(events)):
            calls = llm.call(
                "write module",
                tools=[{"type": "function", "function": {
                    "name": "write_module_design", "parameters": {}}}],
            )
        self.assertEqual(calls[0]["name"], "write_module_design")
        self.assertEqual(json.loads(calls[0]["arguments"]), {"design": json.loads(raw)})
        # Do not turn an unrelated or wrong-depth call into a valid writer.
        self.assertEqual(llm._normalize_architecture_tool_arguments("lookup", raw), raw)
        self.assertEqual(llm._normalize_architecture_tool_arguments(
            "write_architecture_blueprint", raw), raw)
        self.assertEqual(llm._normalize_architecture_tool_arguments(
            "write_module_design", '{"design":{"depth":1}}'),
            '{"design":{"depth":1}}')

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

    async def test_async_flat_architecture_writer_call_is_wrapped(self):
        raw = '{"depth":2,"design_id":"impl-tasks","implementation_units":[]}'
        item = SimpleNamespace(
            type="function_call", call_id="call-async-flat",
            name="write_implementation_design", arguments=raw,
        )
        events = [
            SimpleNamespace(type="response.output_item.done", item=item),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(
                id="r-async-flat", status="completed", usage=None)),
        ]
        llm = _llm()
        with patch.object(llm, "_get_async_client", return_value=_FakeAsyncClient(events)):
            calls = await llm.acall(
                "write implementation",
                tools=[{"type": "function", "function": {
                    "name": "write_implementation_design", "parameters": {}}}],
            )
        self.assertEqual(json.loads(calls[0]["arguments"]), {"design": json.loads(raw)})

    async def test_async_non_streaming_is_rejected_before_transport(self):
        with self.assertRaisesRegex(RuntimeError, "requires streaming"):
            await _llm(stream=False).acall("hello")

    async def test_async_response_headers_have_creation_deadline(self):
        llm = _llm()
        with patch.dict(os.environ, {"PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "0.01"}):
            with patch.object(llm, "_get_async_client", return_value=SimpleNamespace(
                responses=_BlockingAsyncCreateResponses()
            )):
                with self.assertRaisesRegex(TimeoutError, "SSE create exceeded 0.01s"):
                    await llm.acall("headers stall")

    async def test_blocking_async_sse_fails_fast_and_writes_transport_diagnostic(self):
        llm = _llm()
        stream = _BlockingAsyncStream()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sse.jsonl")
            env = {
                "PROJECTOS_RESPONSES_READ_TIMEOUT_SECONDS": "0.01",
                "PROJECTOS_SSE_DIAGNOSTICS_PATH": path,
            }
            with patch.dict(os.environ, env):
                with patch.object(llm, "_get_async_client", return_value=_BlockingAsyncClient(stream)):
                    with self.assertRaisesRegex(TimeoutError, "0.01s without an event"):
                        await llm.acall("stall")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
        transport = next(record for record in records if record["kind"] == "transport_error")
        self.assertEqual(transport["error_type"], "TimeoutError")
        self.assertEqual(transport["mode"], "async")
        self.assertFalse(records[-1]["terminal"])

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

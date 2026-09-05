"""ProjectOS adapter for CrewAI's native OpenAI Responses implementation.

CrewAI already owns the protocol details for Responses streaming, tool-call
item conversion, available-function execution, and structured outputs.  This
module adds the ProjectOS transport policy: Portdan Responses calls must use
SSE streaming and must never silently fall back to a non-streaming request.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from crewai.events.types.llm_events import LLMCallType
from crewai.llms.providers.openai.completion import OpenAICompletion


logger = logging.getLogger(__name__)


def _diag_file_event(kind: str, payload: dict[str, Any]) -> None:
    """Persist a compact SSE diagnostic independent of process logging.

    Workers are launched with ``multiprocessing.spawn`` and do not inherit the
    ASGI process' logging handlers.  When a worker exposes
    ``PROJECTOS_SSE_DIAGNOSTICS_PATH``, append one JSON object per lifecycle
    event so transport visibility survives child-process boundaries.  No
    request/response bodies or credentials are written here.
    """
    target = os.environ.get("PROJECTOS_SSE_DIAGNOSTICS_PATH", "").strip()
    if not target:
        return
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **payload,
    }
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
    except OSError:
        # Diagnostics must never alter request semantics.
        logger.debug("responses_sse_diag_persist_failed", exc_info=True)


class OpenAIResponsesLLM(OpenAICompletion):
    """CrewAI OpenAI LLM configured for the Responses wire API.

    The inherited implementation provides the complete ReAct contract:
    function definitions are encoded as Responses tools, streamed function
    calls are collected, ``available_functions`` are executed, and tool
    outputs are returned through the normal CrewAI loop.  ProjectOS keeps a
    strict streaming-only boundary for this provider.
    """

    llm_type: Literal["openai_responses"] = "openai_responses"
    api: Literal["responses"] = "responses"

    @staticmethod
    def _provider_builtins_allowed() -> bool:
        """Return whether provider-hosted Responses tools are explicitly enabled.

        ProjectOS function tools are still allowed: they are supplied by
        ``ToolGateway`` and converted by CrewAI.  This switch only governs
        CrewAI's ``builtin_tools`` field (web search, file search, code
        interpreter, computer use, etc.), which otherwise could bypass the
        ProjectOS authorization and execution-context layers.
        """
        value = os.environ.get("PROJECTOS_ALLOW_PROVIDER_BUILTINS", "false")
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def call(
        self,
        messages: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str | Any:
        logger.info(
            "responses_adapter_call adapter=%s.%s provider=%s model=%s host=%s stream=%s api=%s",
            type(self).__module__, type(self).__name__, getattr(self, "provider", None),
            getattr(self, "model", None), self._base_url_host(), self._effective_stream(),
            getattr(self, "api", None),
        )
        if not self._effective_stream():
            raise RuntimeError(
                "Responses API adapter requires streaming; "
                "PROJECTOS_LLM_STREAM=false is unsupported for this provider"
            )
        if self.builtin_tools and not self._provider_builtins_allowed():
            names = ", ".join(str(name) for name in self.builtin_tools)
            raise RuntimeError(
                "Provider-hosted Responses tools are disabled by default; "
                "remove builtin_tools or set PROJECTOS_ALLOW_PROVIDER_BUILTINS=true "
                f"for an explicit opt-in (requested: {names})"
            )
        result = super().call(
            messages=messages,
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
            response_model=response_model,
        )
        # CrewAI's native handler can return an empty string when a relay
        # closes an SSE stream without a terminal output.  Treat that as a
        # transport failure so the orchestration layer stops at the first
        # genuine stall instead of marking the node complete.
        if isinstance(result, str) and not result.strip():
            raise RuntimeError(
                "Responses API stream ended without a terminal text or tool result"
            )
        return result

    async def acall(
        self,
        messages: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str | Any:
        """Async counterpart of :meth:`call` with the same policy guards."""
        logger.info(
            "responses_adapter_acall adapter=%s.%s provider=%s model=%s host=%s stream=%s api=%s",
            type(self).__module__, type(self).__name__, getattr(self, "provider", None),
            getattr(self, "model", None), self._base_url_host(), self._effective_stream(),
            getattr(self, "api", None),
        )
        if not self._effective_stream():
            raise RuntimeError(
                "Responses API adapter requires streaming; "
                "PROJECTOS_LLM_STREAM=false is unsupported for this provider"
            )
        if self.builtin_tools and not self._provider_builtins_allowed():
            names = ", ".join(str(name) for name in self.builtin_tools)
            raise RuntimeError(
                "Provider-hosted Responses tools are disabled by default; "
                "remove builtin_tools or set PROJECTOS_ALLOW_PROVIDER_BUILTINS=true "
                f"for an explicit opt-in (requested: {names})"
            )
        result = await super().acall(
            messages=messages,
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
            response_model=response_model,
        )
        if isinstance(result, str) and not result.strip():
            raise RuntimeError(
                "Responses API stream ended without a terminal text or tool result"
            )
        return result

    def _base_url_host(self) -> str | None:
        """Return only the configured base URL hostname for safe diagnostics."""
        value = getattr(self, "base_url", None) or getattr(self, "_base_url", None)
        if not value:
            return None
        try:
            from urllib.parse import urlparse
            return urlparse(str(value)).hostname
        except Exception:
            return None

    def _prepare_responses_params(self, messages, tools=None, response_model=None):
        """Prepare a provider-safe Responses request.

        CrewAI exposes ``seed`` as a common LLM field and its stock Responses
        adapter forwards it verbatim.  The Responses contract used by
        Portdan (and the OpenAI Responses endpoint) does not accept that
        Chat-Completions-only parameter, so filter it at the protocol
        boundary.  Keeping the guard here also protects callers other than
        Planner that may still construct an LLM with legacy common kwargs.
        """
        params = super()._prepare_responses_params(
            messages=messages,
            tools=tools,
            response_model=response_model,
        )
        params.pop("seed", None)
        return params

    def supports_function_calling(self) -> bool:
        """Responses supports the same function-calling contract as CrewAI."""
        return super().supports_function_calling()

    def _sse_diag_start(self, *, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        """Create low-sensitivity diagnostics for one Responses stream.

        The relay's response body is intentionally never logged.  We retain
        event names/counts and byte estimates derived from deltas so an empty
        stream can be distinguished from a stream that contained only tool
        arguments.
        """
        state: dict[str, Any] = {
            "mode": mode,
            "events": {},
            "event_count": 0,
            "delta_bytes": 0,
            "text_chars": 0,
            "argument_chars": 0,
            "response_id": None,
            "terminal": False,
            "terminal_status": None,
            "function_calls": 0,
        }
        logger.info(
            "responses_sse_start adapter=%s.%s mode=%s stream=%s input_items=%s tools=%s",
            type(self).__module__, type(self).__name__, mode, bool(params.get("stream")),
            len(params.get("input") or []) if isinstance(params.get("input"), list) else 1,
            len(params.get("tools") or []) if isinstance(params.get("tools"), list) else 0,
        )
        _diag_file_event("start", {
            "adapter": f"{type(self).__module__}.{type(self).__name__}",
            "mode": mode,
            "stream": bool(params.get("stream")),
            "input_items": len(params.get("input") or []) if isinstance(params.get("input"), list) else 1,
            "tools": len(params.get("tools") or []) if isinstance(params.get("tools"), list) else 0,
            "provider": getattr(self, "provider", None),
            "model": getattr(self, "model", None),
            "host": self._base_url_host(),
            "api": getattr(self, "api", None),
        })
        return state

    def _sse_diag_event(self, state: dict[str, Any], event: Any) -> str:
        event_type = str(getattr(event, "type", "<missing>"))
        state["event_count"] += 1
        events = state["events"]
        events[event_type] = int(events.get(event_type, 0)) + 1
        if event_type == "response.output_text.delta":
            delta = str(getattr(event, "delta", "") or "")
            state["text_chars"] += len(delta)
            state["delta_bytes"] += len(delta.encode("utf-8"))
        elif event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            delta = str(getattr(event, "delta", "") or getattr(event, "arguments", "") or "")
            state["argument_chars"] += len(delta)
            state["delta_bytes"] += len(delta.encode("utf-8"))
        elif event_type == "response.created":
            response = getattr(event, "response", None)
            state["response_id"] = getattr(response, "id", None)
        elif event_type == "response.completed":
            response = getattr(event, "response", None)
            state["terminal"] = True
            state["terminal_status"] = getattr(response, "status", None)
            state["response_id"] = getattr(response, "id", None) or state["response_id"]
        logger.debug(
            "responses_sse_event type=%s response_id=%s index=%s",
            event_type, state.get("response_id"), state["event_count"],
        )
        _diag_file_event("event", {
            "type": event_type,
            "index": state["event_count"],
            "response_id": state.get("response_id"),
            "delta_bytes": state.get("delta_bytes", 0),
            "text_chars": state.get("text_chars", 0),
            "argument_chars": state.get("argument_chars", 0),
        })
        return event_type

    def _sse_diag_finish(self, state: dict[str, Any]) -> None:
        # A stream can terminate through normal completion, a transport
        # exception, or an exception raised while decoding an event.  Keep
        # the lifecycle record exactly-once so callers can always distinguish
        # an empty/unterminated stream from a logging gap.
        if state.get("_finished"):
            return
        state["_finished"] = True
        level = logging.INFO if state["terminal"] else logging.WARNING
        logger.log(
            level,
            "responses_sse_end adapter=%s.%s mode=%s response_id=%s terminal=%s status=%s "
            "events=%s event_types=%s delta_bytes=%s text_chars=%s argument_chars=%s function_calls=%s",
            type(self).__module__, type(self).__name__, state["mode"], state.get("response_id"),
            state["terminal"], state.get("terminal_status"), state["event_count"],
            state["events"], state["delta_bytes"], state["text_chars"],
            state["argument_chars"], state["function_calls"],
        )
        _diag_file_event("end", {
            "adapter": f"{type(self).__module__}.{type(self).__name__}",
            "mode": state["mode"],
            "response_id": state.get("response_id"),
            "terminal": state["terminal"],
            "status": state.get("terminal_status"),
            "event_count": state["event_count"],
            "event_types": state["events"],
            "delta_bytes": state["delta_bytes"],
            "text_chars": state["text_chars"],
            "argument_chars": state["argument_chars"],
            "function_calls": state["function_calls"],
        })

    def _iter_sse_events(self, stream: Any, state: dict[str, Any]):
        """Yield sync SSE events and close diagnostics on every exit path."""
        try:
            yield from stream
        except Exception as error:
            logger.exception("responses_sse_transport_error mode=sync stage=iterate")
            _diag_file_event("transport_error", {
                "mode": "sync",
                "stage": "iterate",
                "error_type": type(error).__name__,
            })
            raise
        finally:
            self._sse_diag_finish(state)

    async def _aiter_sse_events(self, stream: Any, state: dict[str, Any]):
        """Yield async SSE events and close diagnostics on every exit path."""
        try:
            async for event in stream:
                yield event
        except Exception as error:
            logger.exception("responses_sse_transport_error mode=async stage=iterate")
            _diag_file_event("transport_error", {
                "mode": "async",
                "stage": "iterate",
                "error_type": type(error).__name__,
            })
            raise
        finally:
            self._sse_diag_finish(state)

    def _sse_diag_transport(self, state: dict[str, Any], stream: Any) -> None:
        """Record safe transport metadata exposed by the SDK stream wrapper."""
        response = getattr(stream, "response", None) or getattr(stream, "_response", None)
        if response is None:
            return
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", None)
        header_names = (
            sorted(str(key).lower() for key in headers.keys())
            if headers is not None and hasattr(headers, "keys")
            else []
        )
        content_type = None
        if headers is not None:
            getter = getattr(headers, "get", None)
            if callable(getter):
                content_type = getter("content-type") or getter("Content-Type")
        content_type = str(content_type) if content_type else None
        state["http_status"] = status
        state["header_names"] = header_names
        state["content_type"] = content_type
        logger.info(
            "responses_sse_transport mode=%s http_status=%s content_type=%s header_names=%s",
            state["mode"], status, content_type, header_names,
        )
        _diag_file_event("transport", {
            "mode": state["mode"],
            "http_status": status,
            "content_type": content_type,
            "header_names": header_names,
        })

    def _handle_streaming_responses(
        self,
        params: dict[str, Any],
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str | Any:
        """Handle streamed Responses while preserving native tool calls.

        CrewAI's stock streaming handler executes tools when
        ``available_functions`` is supplied, but its native-agent path passes
        ``available_functions=None`` intentionally and expects the raw list of
        Responses function-call items back.  The stock handler falls through
        to an empty text response in that case, which the executor rejects as
        ``Invalid response from LLM call - None or empty``.  Keep the upstream
        implementation's protocol handling, adding the missing return branch.
        """
        full_response = ""
        function_calls: list[dict[str, Any]] = []
        final_response: Any | None = None
        usage: dict[str, Any] | None = None

        diag = self._sse_diag_start(mode="sync", params=params)
        try:
            stream = self._get_sync_client().responses.create(**params)
        except Exception as error:
            logger.exception("responses_sse_transport_error mode=sync stage=create")
            _diag_file_event("transport_error", {
                "mode": "sync", "stage": "create", "error_type": type(error).__name__
            })
            self._sse_diag_finish(diag)
            raise
        self._sse_diag_transport(diag, stream)
        response_id_stream = None
        argument_deltas: dict[str, str] = {}
        function_names: dict[str, str] = {}

        for event in self._iter_sse_events(stream, diag):
            event_type = self._sse_diag_event(diag, event)
            if event_type == "response.created":
                response_id_stream = event.response.id

            if event_type == "response.output_text.delta":
                delta_text = event.delta or ""
                full_response += delta_text
                self._emit_stream_chunk_event(
                    chunk=delta_text,
                    from_task=from_task,
                    from_agent=from_agent,
                    response_id=response_id_stream,
                )
            elif event_type == "response.function_call_arguments.delta":
                call_id = str(getattr(event, "call_id", "") or "")
                argument_deltas[call_id] = argument_deltas.get(call_id, "") + str(getattr(event, "delta", "") or "")
                # CrewAI's progress monitor counts streamed chunks.  Tool
                # argument deltas are response bytes too, even without text.
                self._emit_stream_chunk_event(
                    chunk=str(getattr(event, "delta", "") or ""),
                    from_task=from_task,
                    from_agent=from_agent,
                    response_id=response_id_stream,
                )
            elif event_type == "response.function_call_arguments.done":
                call_id = str(getattr(event, "call_id", "") or "")
                arguments = str(getattr(event, "arguments", "") or "")
                if call_id and arguments:
                    argument_deltas[call_id] = arguments
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", None) == "function_call":
                    call_id = str(getattr(item, "call_id", "") or "")
                    if call_id:
                        function_names[call_id] = str(getattr(item, "name", "") or "")
            elif event_type == "response.output_item.done":
                item = event.item
                if item.type == "function_call":
                    call_id = str(getattr(item, "call_id", "") or "")
                    arguments = getattr(item, "arguments", None) or argument_deltas.get(call_id, "")
                    function_calls.append(
                        {
                            "id": call_id,
                            "name": getattr(item, "name", None) or function_names.get(call_id, ""),
                            "arguments": arguments,
                        }
                    )
                    diag["function_calls"] += 1
            elif event_type == "response.completed":
                final_response = event.response
                if self.auto_chain and event.response and event.response.id:
                    self._last_response_id = event.response.id
                if self.auto_chain_reasoning and event.response:
                    reasoning_items = self._extract_reasoning_items(event.response)
                    if reasoning_items:
                        self._last_reasoning_items = reasoning_items
                if event.response and event.response.usage:
                    usage = self._extract_responses_token_usage(event.response)
                    self._track_token_usage_internal(usage)

        finish_reason, response_id = (
            self._extract_responses_finish_reason_and_id(final_response)
            if final_response is not None
            else (None, response_id_stream)
        )

        if self.parse_tool_outputs and final_response:
            parsed_result = self._extract_builtin_tool_outputs(final_response)
            parsed_result.text = self._apply_stop_words(parsed_result.text)
            self._emit_call_completed_event(
                response=parsed_result.text,
                call_type=LLMCallType.LLM_CALL,
                from_task=from_task,
                from_agent=from_agent,
                messages=params.get("input", []),
                usage=usage,
                finish_reason=finish_reason,
                response_id=response_id,
            )
            return parsed_result

        # Native CrewAI agents need the raw Responses function-call list so
        # they can execute it through their own ToolGateway-aware dispatcher.
        if function_calls and not available_functions:
            self._emit_call_completed_event(
                response=function_calls,
                call_type=LLMCallType.TOOL_CALL,
                from_task=from_task,
                from_agent=from_agent,
                messages=params.get("input", []),
                usage=usage,
                finish_reason=finish_reason,
                response_id=response_id,
            )
            return function_calls

        if function_calls and available_functions:
            for call in function_calls:
                function_name = call.get("name", "")
                function_args = call.get("arguments", {})
                if isinstance(function_args, str):
                    try:
                        function_args = json.loads(function_args)
                    except json.JSONDecodeError:
                        function_args = {}
                result = self._handle_tool_execution(
                    function_name=function_name,
                    function_args=function_args,
                    available_functions=available_functions,
                    from_task=from_task,
                    from_agent=from_agent,
                )
                if result is not None:
                    return result

        if response_model:
            try:
                structured_result = self._validate_structured_output(
                    full_response, response_model
                )
                self._emit_call_completed_event(
                    response=structured_result,
                    call_type=LLMCallType.LLM_CALL,
                    from_task=from_task,
                    from_agent=from_agent,
                    messages=params.get("input", []),
                    usage=usage,
                    finish_reason=finish_reason,
                    response_id=response_id,
                )
                return structured_result
            except ValueError as error:
                logging.warning("Structured output validation failed: %s", error)

        full_response = self._apply_stop_words(full_response)
        self._emit_call_completed_event(
            response=full_response,
            call_type=LLMCallType.LLM_CALL,
            from_task=from_task,
            from_agent=from_agent,
            messages=params.get("input", []),
            usage=usage,
            finish_reason=finish_reason,
            response_id=response_id,
        )
        return self._invoke_after_llm_call_hooks(
            params.get("input", []), full_response, from_agent
        )

    async def _ahandle_streaming_responses(
        self,
        params: dict[str, Any],
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str | Any:
        """Handle async Responses streaming while preserving native tool calls.

        CrewAI's native-agent path intentionally passes ``available_functions``
        as ``None`` and expects the raw function-call list to be returned.  The
        upstream async handler only executes calls when a function map is
        supplied, so it otherwise falls through to an empty text response.
        """
        full_response = ""
        function_calls: list[dict[str, Any]] = []
        final_response: Any | None = None
        usage: dict[str, Any] | None = None

        diag = self._sse_diag_start(mode="async", params=params)
        try:
            stream = await self._get_async_client().responses.create(**params)
        except Exception as error:
            logger.exception("responses_sse_transport_error mode=async stage=create")
            _diag_file_event("transport_error", {
                "mode": "async", "stage": "create", "error_type": type(error).__name__
            })
            self._sse_diag_finish(diag)
            raise
        self._sse_diag_transport(diag, stream)
        response_id_stream = None
        argument_deltas: dict[str, str] = {}
        function_names: dict[str, str] = {}

        async for event in self._aiter_sse_events(stream, diag):
            event_type = self._sse_diag_event(diag, event)
            if event_type == "response.created":
                response_id_stream = event.response.id

            if event_type == "response.output_text.delta":
                delta_text = event.delta or ""
                full_response += delta_text
                self._emit_stream_chunk_event(
                    chunk=delta_text,
                    from_task=from_task,
                    from_agent=from_agent,
                    response_id=response_id_stream,
                )
            elif event_type == "response.function_call_arguments.delta":
                call_id = str(getattr(event, "call_id", "") or "")
                argument_deltas[call_id] = argument_deltas.get(call_id, "") + str(getattr(event, "delta", "") or "")
                self._emit_stream_chunk_event(
                    chunk=str(getattr(event, "delta", "") or ""),
                    from_task=from_task,
                    from_agent=from_agent,
                    response_id=response_id_stream,
                )
            elif event_type == "response.function_call_arguments.done":
                call_id = str(getattr(event, "call_id", "") or "")
                arguments = str(getattr(event, "arguments", "") or "")
                if call_id and arguments:
                    argument_deltas[call_id] = arguments
            elif event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", None) == "function_call":
                    call_id = str(getattr(item, "call_id", "") or "")
                    if call_id:
                        function_names[call_id] = str(getattr(item, "name", "") or "")
            elif event_type == "response.output_item.done":
                item = event.item
                if item.type == "function_call":
                    call_id = str(getattr(item, "call_id", "") or "")
                    arguments = getattr(item, "arguments", None) or argument_deltas.get(call_id, "")
                    function_calls.append(
                        {
                            "id": call_id,
                            "name": getattr(item, "name", None) or function_names.get(call_id, ""),
                            "arguments": arguments,
                        }
                    )
                    diag["function_calls"] += 1
            elif event_type == "response.completed":
                final_response = event.response
                if self.auto_chain and event.response and event.response.id:
                    self._last_response_id = event.response.id
                if self.auto_chain_reasoning and event.response:
                    reasoning_items = self._extract_reasoning_items(event.response)
                    if reasoning_items:
                        self._last_reasoning_items = reasoning_items
                if event.response and event.response.usage:
                    usage = self._extract_responses_token_usage(event.response)
                    self._track_token_usage_internal(usage)

        finish_reason, response_id = (
            self._extract_responses_finish_reason_and_id(final_response)
            if final_response is not None
            else (None, response_id_stream)
        )

        if self.parse_tool_outputs and final_response:
            parsed_result = self._extract_builtin_tool_outputs(final_response)
            parsed_result.text = self._apply_stop_words(parsed_result.text)
            self._emit_call_completed_event(
                response=parsed_result.text,
                call_type=LLMCallType.LLM_CALL,
                from_task=from_task,
                from_agent=from_agent,
                messages=params.get("input", []),
                usage=usage,
                finish_reason=finish_reason,
                response_id=response_id,
            )
            return parsed_result

        if function_calls and not available_functions:
            self._emit_call_completed_event(
                response=function_calls,
                call_type=LLMCallType.TOOL_CALL,
                from_task=from_task,
                from_agent=from_agent,
                messages=params.get("input", []),
                usage=usage,
                finish_reason=finish_reason,
                response_id=response_id,
            )
            return function_calls

        if function_calls and available_functions:
            for call in function_calls:
                function_name = call.get("name", "")
                function_args = call.get("arguments", {})
                if isinstance(function_args, str):
                    try:
                        function_args = json.loads(function_args)
                    except json.JSONDecodeError:
                        function_args = {}
                result = self._handle_tool_execution(
                    function_name=function_name,
                    function_args=function_args,
                    available_functions=available_functions,
                    from_task=from_task,
                    from_agent=from_agent,
                )
                if result is not None:
                    return result

        if response_model:
            try:
                structured_result = self._validate_structured_output(
                    full_response, response_model
                )
                self._emit_call_completed_event(
                    response=structured_result,
                    call_type=LLMCallType.LLM_CALL,
                    from_task=from_task,
                    from_agent=from_agent,
                    messages=params.get("input", []),
                    usage=usage,
                    finish_reason=finish_reason,
                    response_id=response_id,
                )
                return structured_result
            except ValueError as error:
                logging.warning("Structured output validation failed: %s", error)

        full_response = self._apply_stop_words(full_response)
        self._emit_call_completed_event(
            response=full_response,
            call_type=LLMCallType.LLM_CALL,
            from_task=from_task,
            from_agent=from_agent,
            messages=params.get("input", []),
            usage=usage,
            finish_reason=finish_reason,
            response_id=response_id,
        )
        return self._invoke_after_llm_call_hooks(
            params.get("input", []), full_response, from_agent
        )

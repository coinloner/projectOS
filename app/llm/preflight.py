"""Cheap, bounded provider capability preflight.

The preflight is intentionally separate from CrewAI.  It verifies that the
selected endpoint can accept the wire protocol ProjectOS is about to use
(streaming plus function tools) before a long multi-node run is started.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Callable

import httpx

from app.llm.config import LLMSelection


@dataclass(frozen=True)
class ProviderPreflightResult:
    provider: str
    model: str
    wire_api: str
    endpoint: str
    status: str  # passed | blocked | unavailable | failed
    retryable: bool
    message: str
    http_status: int | None = None
    terminal_seen: bool = False
    tool_schema_accepted: bool = False
    elapsed_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "wire_api": self.wire_api,
            "endpoint": self.endpoint,
            "status": self.status,
            "retryable": self.retryable,
            "message": self.message,
            "http_status": self.http_status,
            "terminal_seen": self.terminal_seen,
            "tool_schema_accepted": self.tool_schema_accepted,
            "elapsed_ms": self.elapsed_ms,
        }


class ProviderPreflightError(RuntimeError):
    """Raised when a provider cannot be used for the selected run."""

    def __init__(self, result: ProviderPreflightResult) -> None:
        super().__init__(result.message)
        self.result = result


class ProviderPreflight:
    """Run one bounded, non-project-mutating provider capability probe."""

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        if timeout_seconds is None:
            try:
                timeout_seconds = float(
                    os.environ.get("PROJECTOS_PROVIDER_PREFLIGHT_TIMEOUT_SECONDS", "15")
                )
            except (TypeError, ValueError):
                timeout_seconds = 15.0
        self.timeout_seconds = max(1.0, timeout_seconds)
        self._client_factory = client_factory

    def check(self, selection: LLMSelection) -> ProviderPreflightResult:
        started = time.monotonic()
        endpoint = self._endpoint(selection)
        headers = {key: value for key, value in selection.http_headers}
        api_key = os.environ.get(selection.api_key_env, "").strip()
        if not api_key:
            result = self._result(
                selection,
                endpoint,
                started,
                status="unavailable",
                retryable=False,
                message=f"未找到 {selection.api_key_env}，无法执行 Provider preflight",
            )
            raise ProviderPreflightError(result)
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Accept"] = "text/event-stream, application/json"
        headers["Content-Type"] = "application/json"
        payload = self._payload(selection)
        try:
            with self._client_factory(
                timeout=httpx.Timeout(self.timeout_seconds, connect=self.timeout_seconds)
            ) as client:
                with client.stream(
                    "POST", endpoint, headers=headers, json=payload
                ) as response:
                    status_code = response.status_code
                    if status_code >= 400:
                        body = response.read().decode("utf-8", errors="replace")[:500]
                        blocked = status_code in {400, 401, 403, 404, 405, 422}
                        result = self._result(
                            selection,
                            endpoint,
                            started,
                            status="blocked" if blocked else "unavailable",
                            retryable=not blocked,
                            message=(
                                f"Provider preflight HTTP {status_code}: "
                                f"{self._safe_error(body)}"
                            ),
                            http_status=status_code,
                            tool_schema_accepted=False,
                        )
                        raise ProviderPreflightError(result)
                    terminal_seen = False
                    tool_schema_accepted = True
                    for line in response.iter_lines():
                        text = str(line).strip()
                        if not text:
                            continue
                        if "[DONE]" in text or any(
                            marker in text
                            for marker in (
                                "response.completed",
                                "response.done",
                                "message_stop",
                                "finish_reason",
                                "completed",
                            )
                        ):
                            terminal_seen = True
                            break
                    if not terminal_seen:
                        result = self._result(
                            selection,
                            endpoint,
                            started,
                            status="failed",
                            retryable=True,
                            message="Provider preflight 未观察到 terminal event",
                            http_status=status_code,
                            terminal_seen=False,
                            tool_schema_accepted=tool_schema_accepted,
                        )
                        raise ProviderPreflightError(result)
                    return self._result(
                        selection,
                        endpoint,
                        started,
                        status="passed",
                        retryable=False,
                        message="Provider 支持当前 streaming/tool calling 协议",
                        http_status=status_code,
                        terminal_seen=True,
                        tool_schema_accepted=tool_schema_accepted,
                    )
        except ProviderPreflightError:
            raise
        except (httpx.TimeoutException, httpx.TransportError, OSError) as error:
            result = self._result(
                selection,
                endpoint,
                started,
                status="unavailable",
                retryable=True,
                message=f"Provider preflight transport failure: {type(error).__name__}",
            )
            raise ProviderPreflightError(result) from error

    @staticmethod
    def _endpoint(selection: LLMSelection) -> str:
        suffix = "/responses" if selection.wire_api == "responses" else "/chat/completions"
        return selection.base_url.rstrip("/") + suffix

    @staticmethod
    def _payload(selection: LLMSelection) -> dict[str, Any]:
        tool = {
            "type": "function",
            "name": "projectos_preflight_echo",
            "description": "Capability probe only; do not call unless needed.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }
        if selection.wire_api == "responses":
            return {
                "model": selection.model,
                "input": "Reply with the single word OK.",
                "stream": True,
                "max_output_tokens": 16,
                "tools": [tool],
            }
        return {
            "model": selection.model,
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
            "stream": True,
            "max_tokens": 8,
            "tools": [{"type": "function", "function": {key: value for key, value in tool.items() if key != "type"}}],
        }

    @staticmethod
    def _safe_error(body: str) -> str:
        # Do not persist response bodies: providers may echo request details or
        # sensitive policy metadata. Keep only a bounded diagnostic marker.
        compact = " ".join(body.split())
        return compact[:300] or "empty response"

    def _result(
        self,
        selection: LLMSelection,
        endpoint: str,
        started: float,
        *,
        status: str,
        retryable: bool,
        message: str,
        http_status: int | None = None,
        terminal_seen: bool = False,
        tool_schema_accepted: bool = False,
    ) -> ProviderPreflightResult:
        return ProviderPreflightResult(
            provider=selection.provider,
            model=selection.model,
            wire_api=selection.wire_api,
            endpoint=endpoint,
            status=status,
            retryable=retryable,
            message=message,
            http_status=http_status,
            terminal_seen=terminal_seen,
            tool_schema_accepted=tool_schema_accepted,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

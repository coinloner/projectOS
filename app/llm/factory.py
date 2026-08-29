"""从 ProjectOS 配置构造 CrewAI LLM。"""

from __future__ import annotations

import os

from crewai import LLM
from dotenv import load_dotenv

from app.llm.config import LLMSelection, resolve_llm_selection


def build_llm(
    provider: str | None = None,
    *,
    temperature: float | None = None,
    seed: int | None = None,
    stream: bool | None = None,
    selection: LLMSelection | None = None,
) -> LLM:
    """加载项目 .env 后创建当前配置的 CrewAI LLM，不发起模型请求。"""
    load_dotenv(override=False)
    selected = selection or resolve_llm_selection(provider)
    api_key_env = selected.api_key_env
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"❌ 未找到 {api_key_env}，请在项目根目录 .env 文件中设置")

    if stream is None:
        stream = os.environ.get("PROJECTOS_LLM_STREAM", "true").lower() not in {
            "0", "false", "no", "off"
        }

    # Providers occasionally keep a malformed tool-calling turn open while
    # emitting unbounded text.  The token cap makes the provider return a
    # terminal finish reason; the request timeout is a final transport guard.
    # Both are deployment settings so a slow/large model can opt in to larger
    # values without code changes.
    def _positive_int(name: str, default: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def _positive_float(name: str, default: float) -> float:
        try:
            value = float(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return LLM(
        model=selected.model,
        api_key=api_key,
        base_url=selected.base_url,
        provider=selected.crewai_provider,
        temperature=temperature,
        seed=seed,
        stream=stream,
        max_tokens=_positive_int("PROJECTOS_LLM_MAX_TOKENS", 12000),
        timeout=_positive_float("PROJECTOS_LLM_TIMEOUT_SECONDS", 600.0),
    )

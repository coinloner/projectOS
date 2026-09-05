"""CrewAI LLM 的 provider 预设和运行时选择。"""

import os
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values, load_dotenv

# ── 厂商预设 ──────────────────────────────────
# 添加新厂商只需在此字典中增加一条记录。
_PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "crewai_provider": "deepseek",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "api_key_env": "OPENAI_API_KEY",
        "crewai_provider": "openai",
    },
    # SiliconFlow exposes an OpenAI-compatible chat-completions endpoint.
    # Keep CrewAI's provider as ``openai`` while using SiliconFlow's model id.
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "api_key_env": "SILICONFLOW_API_KEY",
        "crewai_provider": "openai",
    },
    "fhl": {
        "base_url": "https://www.fhl.mom/v1",
        "model": "gpt-5.6-terra",
        "api_key_env": "FHL_API_KEY",
        "crewai_provider": "openai",
    },
    "claude": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-5-20250901",
        "api_key_env": "ANTHROPIC_API_KEY",
        "crewai_provider": "anthropic",
    },
    "portdan": {
        "base_url": "https://portdan.com",
        "model": "gpt-5.5",
        "api_key_env": "PORTDAN_API_KEY",
        "crewai_provider": "openai",
        "wire_api": "responses",
    },
}

# ── 当前激活厂商 ──────────────────────────────
# 环境变量 PROJECTOS_LLM_PROVIDER 的优先级高于此默认值。
ACTIVE_PROVIDER: str = "fhl"


@dataclass(frozen=True)
class LLMSelection:
    """一轮运行固定使用的 LLM 配置，不包含 API key 内容。"""

    provider: str
    model: str
    base_url: str
    api_key_env: str
    crewai_provider: str
    wire_api: str = "chat_completions"

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "crewai_provider": self.crewai_provider,
            "wire_api": self.wire_api,
        }


def get_provider_config(provider: str | None = None) -> dict[str, str]:
    """返回运行时选择的 CrewAI 配置。

    选择顺序：显式 ``provider`` > ``PROJECTOS_LLM_PROVIDER`` > 默认 provider。
    ``PROJECTOS_LLM_MODEL`` 与 ``PROJECTOS_LLM_BASE_URL`` 可覆盖预设，便于在
    不改动代码的情况下切换同一 provider 下的模型或兼容端点。
    """
    name = provider or os.environ.get("PROJECTOS_LLM_PROVIDER") or ACTIVE_PROVIDER
    name = name.strip().lower()
    if name not in _PROVIDERS:
        raise RuntimeError(
            f"❌ 未知的 LLM 厂商: {name}，可用: {list(_PROVIDERS.keys())}"
        )
    config = dict(_PROVIDERS[name])
    # Provider/model are selected by the request layer. Environment variables
    # remain accepted as a backwards-compatible deployment override.
    model = os.environ.get("PROJECTOS_LLM_MODEL")
    base_url = os.environ.get("PROJECTOS_LLM_BASE_URL")
    if model:
        config["model"] = model.strip()
    if base_url:
        config["base_url"] = base_url.strip()
    return config


def resolve_llm_selection(
    provider: str | None = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMSelection:
    """解析一轮运行的 provider/model，优先使用显式请求值。"""
    config = get_provider_config(provider)
    if model and model.strip():
        config["model"] = model.strip()
    if base_url and base_url.strip():
        config["base_url"] = base_url.strip()
    return LLMSelection(
        provider=(provider or os.environ.get("PROJECTOS_LLM_PROVIDER") or ACTIVE_PROVIDER).strip().lower(),
        model=config["model"],
        base_url=config["base_url"],
        api_key_env=config["api_key_env"],
        crewai_provider=config["crewai_provider"],
        wire_api=config.get("wire_api", "chat_completions"),
    )


def available_provider_configs() -> tuple[dict[str, str], ...]:
    """返回可供 UI 选择的非敏感 provider 摘要。"""
    return tuple(
        {
            "id": name,
            "model": config["model"],
            "base_url": config["base_url"],
            "api_key_env": config["api_key_env"],
        }
        for name, config in _PROVIDERS.items()
    )


def discover_provider_models(provider: str) -> tuple[str, ...]:
    """从 OpenAI 兼容 provider 动态发现模型 ID，不返回任何凭证。"""
    name = provider.strip().lower()
    config = get_provider_config(name)
    if name not in {"openai", "siliconflow", "fhl", "portdan"}:
        return (config["model"],)

    load_dotenv(override=False)
    api_key = os.environ.get(config["api_key_env"])
    if not api_key:
        api_key = dotenv_values(".env").get(config["api_key_env"])
    if not api_key:
        raise RuntimeError(f"❌ 未找到 {config['api_key_env']}，无法查询模型列表")

    request = Request(
        config["base_url"].rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
        raise RuntimeError(f"查询 {name} 模型列表失败: {error}") from error

    models = payload.get("data", []) if isinstance(payload, dict) else []
    ids = sorted(
        {str(item["id"]).strip() for item in models if isinstance(item, dict) and item.get("id")}
    )
    return tuple(ids) or (config["model"],)

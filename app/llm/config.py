"""CrewAI LLM 的 provider 预设和运行时选择。"""

import os

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
    "claude": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-5-20250901",
        "api_key_env": "ANTHROPIC_API_KEY",
        "crewai_provider": "anthropic",
    },
}

# ── 当前激活厂商 ──────────────────────────────
# 环境变量 PROJECTOS_LLM_PROVIDER 的优先级高于此默认值。
ACTIVE_PROVIDER: str = "deepseek"


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
    model = os.environ.get("PROJECTOS_LLM_MODEL")
    base_url = os.environ.get("PROJECTOS_LLM_BASE_URL")
    if model:
        config["model"] = model.strip()
    if base_url:
        config["base_url"] = base_url.strip()
    return config

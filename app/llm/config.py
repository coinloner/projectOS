"""LLM 配置 —— 切换模型/厂商只需改这里。"""

from typing import Optional

# ── 厂商预设 ──────────────────────────────────
# 添加新厂商只需在此字典中增加一条记录。
_PROVIDERS: dict[str, dict[str, Optional[str]]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "claude": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-5-20250901",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
}

# ── 当前激活厂商 ──────────────────────────────
ACTIVE_PROVIDER: str = "deepseek"


def get_provider_config(provider: Optional[str] = None) -> dict:
    """根据厂商名返回 {base_url, model, api_key_env}。"""
    name = provider or ACTIVE_PROVIDER
    if name not in _PROVIDERS:
        raise RuntimeError(
            f"❌ 未知的 LLM 厂商: {name}，可用: {list(_PROVIDERS.keys())}"
        )
    return _PROVIDERS[name]

"""从 ProjectOS 配置构造 CrewAI LLM。"""

from __future__ import annotations

import os

from crewai import LLM
from dotenv import load_dotenv

from app.llm.config import get_provider_config


def build_llm(provider: str | None = None) -> LLM:
    """加载项目 .env 后创建当前配置的 CrewAI LLM，不发起模型请求。"""
    load_dotenv(override=False)
    config = get_provider_config(provider)
    api_key_env = config["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"❌ 未找到 {api_key_env}，请在项目根目录 .env 文件中设置")

    return LLM(
        model=config["model"],
        api_key=api_key,
        base_url=config["base_url"],
        provider=config["crewai_provider"],
    )

import os
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

from app.llm.config import get_provider_config

load_dotenv(override=True)


class LLMClient:
    """统一的大模型调用入口 —— 对外只暴露 invoke()."""

    def __init__(self, provider: Optional[str] = None) -> None:
        """初始化客户端。

        Args:
            provider: 厂商名（"deepseek" | "openai" | "claude"），
                      不传则使用 config.ACTIVE_PROVIDER。
        """
        config = get_provider_config(provider)

        api_key = os.environ.get(config["api_key_env"])
        if not api_key:
            raise RuntimeError(
                f"❌ 未找到 {config['api_key_env']}，请在项目根目录 .env 文件中设置"
            )

        self._client = OpenAI(
            api_key=api_key,
            base_url=config["base_url"],
        )
        self._model = config["model"]

    def invoke(self, prompt: str) -> str:
        """向 LLM 发送提示词，返回纯文本回复。"""
        if not prompt or not prompt.strip():
            raise RuntimeError("❌ prompt 不能为空")

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            raise RuntimeError(f"❌ LLM 调用失败: {e}") from e

        if not response.choices:
            raise RuntimeError("❌ LLM 返回了空响应（无 choices）")

        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("❌ LLM 返回了空内容")

        return content

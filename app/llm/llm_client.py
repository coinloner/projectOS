import os
from dataclasses import dataclass
from typing import Optional, Union

from dotenv import load_dotenv
from openai import OpenAI

from app.llm.config import get_provider_config

load_dotenv(override=True)


# ── LLMResponse ──────────────────────────────


@dataclass
class LLMResponse:
    """invoke() 的返回值 —— 统一承载文本回复与工具调用请求。

    tool_calls 使用简化格式（方案 B），provider 无关：
        [{"id": "call_xxx", "name": "save", "arguments": "{...}"}]
    """

    content: Optional[str] = None
    tool_calls: Optional[list] = None

    @property
    def is_tool_call(self) -> bool:
        return self.tool_calls is not None and len(self.tool_calls) > 0

    def __str__(self) -> str:
        return self.content or ""


# ── LLMClient ────────────────────────────────


class LLMClient:
    """统一的大模型调用入口。

    invoke() 支持两种调用模式：

        Simple（text-in / text-out）:
            client.invoke("你好") → LLMResponse(content="Hello")

        Agent（messages + tools）:
            client.invoke(messages, tools=[...]) → LLMResponse
            ─ response.is_tool_call == False → response.content 为文本
            ─ response.is_tool_call == True  → response.tool_calls 为简化列表
    """

    def __init__(self, provider: Optional[str] = None) -> None:
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

    # ── invoke ────────────────────────────────

    def invoke(
        self,
        prompt: Union[str, list],
        tools: Optional[list] = None,
    ) -> LLMResponse:
        """向 LLM 发送请求。

        Args:
            prompt: 字符串（Simple）或 messages 列表（Agent）
            tools: 可选，OpenAI 格式的工具定义列表

        Returns:
            LLMResponse ——
              - 文本回复时 content 有值，tool_calls 为 None
              - 工具调用请求时 tool_calls 有值，content 为 None
        """

        messages = (
            [{"role": "user", "content": prompt}]
            if isinstance(prompt, str)
            else prompt
        )

        kwargs: dict = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise RuntimeError(f"❌ LLM 调用失败: {e}") from e

        if not response.choices:
            raise RuntimeError("❌ LLM 返回了空响应（无 choices）")

        msg = response.choices[0].message

        # tool_calls → 简化格式（方案 B）
        if msg.tool_calls:
            return LLMResponse(
                tool_calls=[
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in msg.tool_calls
                ]
            )

        # 普通文本回复
        if msg.content is None:
            raise RuntimeError("❌ LLM 返回了空内容")

        return LLMResponse(content=msg.content)

    # ── 消息构建（provider 格式适配）────────────

    def build_assistant_message(self, tool_calls: list[dict]) -> dict:
        """将简化 tool_calls（方案 B）转为 provider 格式的 assistant 消息。

        当前实现适配 OpenAI / DeepSeek。
        将来切换 Anthropic 等 provider 时在此方法内按 self._provider 分支。
        """
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_calls
            ],
        }

    def build_tool_result(self, call_id: str, result: str) -> dict:
        """构建 provider 格式的 tool 结果消息。"""
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": result,
        }

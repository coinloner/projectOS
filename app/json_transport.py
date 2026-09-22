"""窄边界的结构化 JSON 传输兼容辅助。"""

from __future__ import annotations

import unicodedata


def strip_json_transport_noise(content: str) -> str:
    """仅清除 JSON 载荷边界的传输格式字符。

    某些兼容 OpenAI 协议的中转会在结构化输出前后附加 BOM 或零宽格式字符。
    它们不是 JSON 空白，因而会让本应合法的对象在解析边界失败。这里绝不从
    自然语言中提取 JSON，也不改变对象内部内容；只移除边界的 Unicode 空白和
    ``Cf``（format）字符，然后仍要求整个剩余载荷是一个完整 JSON 值。
    """

    if not isinstance(content, str):
        return content
    start = 0
    end = len(content)
    while start < end and (
        content[start].isspace() or unicodedata.category(content[start]) == "Cf"
    ):
        start += 1
    while end > start and (
        content[end - 1].isspace() or unicodedata.category(content[end - 1]) == "Cf"
    ):
        end -= 1
    return content[start:end]

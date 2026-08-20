"""连续会话的确定性意图分类。"""

from __future__ import annotations

from enum import Enum


class ConversationIntent(str, Enum):
    NEW_REQUEST = "new_request"
    MODIFY_REQUEST = "modify_request"
    CONTINUE = "continue"
    INSPECT_RESULT = "inspect_result"
    RESUME = "resume"
    AUTHORIZE = "authorize"


def classify_intent(content: str) -> ConversationIntent:
    text = " ".join(content.strip().lower().split())
    if any(token in text for token in ("授权", "批准", "允许", "approve", "authorize")):
        return ConversationIntent.AUTHORIZE
    if any(token in text for token in ("恢复", "resume", "从断点")):
        return ConversationIntent.RESUME
    if any(token in text for token in ("查看结果", "查看运行", "运行结果", "为什么失败", "测试怎么样", "状态怎么样", "发生了什么")):
        return ConversationIntent.INSPECT_RESULT
    if any(token in text for token in ("修改", "改成", "换成", "补充", "调整", "修正", "修改需求")):
        return ConversationIntent.MODIFY_REQUEST
    if any(token in text for token in ("继续", "接着", "下一步", "continue")):
        return ConversationIntent.CONTINUE
    return ConversationIntent.NEW_REQUEST

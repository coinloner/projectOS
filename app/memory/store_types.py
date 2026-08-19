"""Memory 索引使用的最小事件协议，避免索引模块反向导入 Store。"""

from __future__ import annotations

from typing import Protocol


class IndexedMemoryEvent(Protocol):
    id: str
    trace_id: str
    sequence: int
    work_item_id: str | None
    role: str
    event_type: str
    tier: str
    lifecycle: str
    retrieval_enabled: bool
    expires_at: str | None
    content: str

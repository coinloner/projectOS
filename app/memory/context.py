"""分层 Memory 上下文组装。

组装器把最近工作记忆、checkpoint 和检索召回分开处理，避免把所有相似事件
直接塞进 Prompt。它只负责连续性，不改变 Policy/Artifact/Trace 的权威性。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.memory.store import MemoryEvent, MemoryStore


_ROLES = ("user", "planner", "assistant", "tool", "control")


@dataclass(frozen=True)
class MemoryContext:
    trace_id: str
    work_item_id: str | None
    recent: tuple[MemoryEvent, ...]
    recalled: tuple[MemoryEvent, ...]
    checkpoint: dict[str, object] | None
    max_chars: int = 8_000

    def as_prompt(self) -> str:
        if not self.recent and not self.recalled and self.checkpoint is None:
            return ""
        lines = [
            "Memory 上下文（仅作执行连续性参考，不覆盖当前 Policy、Artifact 或 Trace 事实）："
        ]
        if self.checkpoint is not None:
            lines.append("当前 checkpoint: " + _json(self.checkpoint))
        if self.recent:
            lines.append("最近工作记忆：")
            for event in self.recent:
                lines.append(_format_event(event))
        if self.recalled:
            lines.append("相关历史事件（由 Memory 检索召回）：")
            for event in self.recalled:
                lines.append(_format_event(event, recalled=True))
        return _fit("\n".join(lines), self.max_chars)


class MemoryContextAssembler:
    """以固定预算合并 L1 工作记忆与 L2/L3 检索结果。"""

    def __init__(
        self,
        store: MemoryStore,
        *,
        recent_limit: int = 8,
        recall_limit: int = 6,
        max_chars: int = 8_000,
    ) -> None:
        if recent_limit < 1 or recall_limit < 0 or max_chars < 500:
            raise ValueError("MemoryContextAssembler 的预算参数无效")
        self._store = store
        self._recent_limit = recent_limit
        self._recall_limit = recall_limit
        self._max_chars = max_chars

    def build(
        self,
        *,
        trace_id: str,
        work_item_id: str | None,
        query: str,
        include_durable: bool = False,
    ) -> MemoryContext:
        recent_view = self._store.view(
            trace_id,
            work_item_id=work_item_id,
            roles=_ROLES,
            limit=self._recent_limit,
            max_chars=max(500, int(self._max_chars * 0.55)),
        )
        recalled = self._store.search(
            query,
            trace_id=trace_id,
            limit=self._recall_limit,
            roles=_ROLES,
        )
        if include_durable and query.strip():
            recalled += self._store.search_durable(query, limit=self._recall_limit)
        recent_ids = {event.id for event in recent_view.events}
        seen = set(recent_ids)
        unique_recalled: list[MemoryEvent] = []
        for event in recalled:
            if event.id in seen:
                continue
            seen.add(event.id)
            unique_recalled.append(event)
        recalled = tuple(unique_recalled)
        return MemoryContext(
            trace_id=trace_id,
            work_item_id=work_item_id,
            recent=recent_view.events,
            recalled=recalled,
            checkpoint=recent_view.checkpoint,
            max_chars=self._max_chars,
        )


def _format_event(event: MemoryEvent, *, recalled: bool = False) -> str:
    label = event.role
    if event.tool_name:
        label += f"/{event.tool_name}"
    if recalled:
        label += ", recalled"
    source = f" source={','.join(event.source_refs)}" if event.source_refs else ""
    tier = f" tier={event.tier}" if event.tier not in {"raw", "working"} else ""
    return f"[{event.sequence}] {label} ({event.event_type}{tier}{source}): {event.content}"


def _fit(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 40] + "\n[Memory 上下文已按预算截断]"


def _json(value: dict[str, object]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)

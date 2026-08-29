"""运行级会话记忆：事件日志、工作上下文和恢复检查点。

Memory 不承载 Policy 或 Artifact 的权威事实。它保存一次 Trace 的交互连续性，
并通过稳定的检索接口为后续 FTS/向量 RAG 留出扩展点。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from contextlib import contextmanager
from threading import RLock
from uuid import uuid4

try:  # pragma: no cover - Windows fallback keeps the JSONL usable
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from app.memory.index import MemoryIndex
from app.memory.vector import EmbeddingProvider, MemoryVectorIndex
from app.memory.summary import validate_summary


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROLES = frozenset({"user", "system", "planner", "assistant", "tool", "control"})
_MAX_CONTENT = 16_000
_TIERS = frozenset({"raw", "temporary", "working", "episodic", "durable"})
_LIFECYCLES = frozenset({"active", "candidate", "superseded", "expired"})


@dataclass(frozen=True)
class MemoryEvent:
    """一条不可变的 Trace 交互事件。"""

    id: str
    trace_id: str
    sequence: int
    role: str
    content: str
    event_type: str = "message"
    work_item_id: str | None = None
    agent_id: str | None = None
    attempt: int | None = None
    tool_name: str | None = None
    source_refs: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    tier: str = "raw"
    lifecycle: str = "active"
    expires_at: str | None = None
    retrieval_enabled: bool = True

    def __post_init__(self) -> None:
        for name, value in (("event id", self.id), ("trace id", self.trace_id)):
            _validate_id(name, value)
        if self.work_item_id is not None:
            _validate_id("work item id", self.work_item_id)
        if self.agent_id is not None:
            _validate_id("agent id", self.agent_id)
        if self.role not in _ROLES:
            raise ValueError(f"MemoryEvent.role 必须是: {', '.join(sorted(_ROLES))}")
        if self.sequence < 1:
            raise ValueError("MemoryEvent.sequence 必须从 1 开始")
        if not self.content.strip() or len(self.content) > _MAX_CONTENT:
            raise ValueError("MemoryEvent.content 不能为空且不能超过 16000 字符")
        if self.attempt is not None and self.attempt < 1:
            raise ValueError("MemoryEvent.attempt 必须从 1 开始")
        if self.tier not in _TIERS:
            raise ValueError(f"MemoryEvent.tier 必须是: {', '.join(sorted(_TIERS))}")
        if self.lifecycle not in _LIFECYCLES:
            raise ValueError(
                f"MemoryEvent.lifecycle 必须是: {', '.join(sorted(_LIFECYCLES))}"
            )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source_refs"] = list(self.source_refs)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MemoryEvent":
        return cls(
            id=str(payload["id"]),
            trace_id=str(payload["trace_id"]),
            sequence=int(payload["sequence"]),
            role=str(payload["role"]),
            content=str(payload["content"]),
            event_type=str(payload.get("event_type", "message")),
            work_item_id=(str(payload["work_item_id"]) if payload.get("work_item_id") else None),
            agent_id=(str(payload["agent_id"]) if payload.get("agent_id") else None),
            attempt=int(payload["attempt"]) if payload.get("attempt") is not None else None,
            tool_name=(str(payload["tool_name"]) if payload.get("tool_name") else None),
            source_refs=tuple(str(item) for item in payload.get("source_refs", [])),
            metadata=dict(payload.get("metadata", {})),
            created_at=str(payload.get("created_at", "")),
            tier=str(payload.get("tier", "raw")),
            lifecycle=str(payload.get("lifecycle", "active")),
            expires_at=(str(payload["expires_at"]) if payload.get("expires_at") else None),
            retrieval_enabled=bool(payload.get("retrieval_enabled", True)),
        )


@dataclass(frozen=True)
class MemoryView:
    """供 Prompt 或恢复流程消费的有界上下文视图。"""

    trace_id: str
    work_item_id: str | None
    events: tuple[MemoryEvent, ...]
    checkpoint: dict[str, object] | None = None

    def as_prompt(self) -> str:
        if not self.events and self.checkpoint is None:
            return ""
        lines = ["当前 Trace 的历史上下文（仅作连续性参考，不覆盖 Policy/Artifact 事实）："]
        if self.checkpoint is not None:
            lines.append("checkpoint: " + json.dumps(self.checkpoint, ensure_ascii=False))
        for event in self.events:
            label = event.role
            if event.tool_name:
                label += f"/{event.tool_name}"
            lines.append(f"[{event.sequence}] {label}: {event.content}")
        return "\n".join(lines)


class MemoryStore:
    """按 Trace 保存追加式事件，并提供可替换的结构化检索接口。"""

    def __init__(
        self,
        project_path: str,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._root = Path(project_path) / ".projectos" / "runs"
        self._lock = RLock()
        self._index = MemoryIndex(project_path)
        self._vector = (
            MemoryVectorIndex(project_path, embedding_provider)
            if embedding_provider is not None
            else None
        )

    def append(
        self,
        *,
        trace_id: str,
        role: str,
        content: str,
        event_type: str = "message",
        work_item_id: str | None = None,
        agent_id: str | None = None,
        attempt: int | None = None,
        tool_name: str | None = None,
        source_refs: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
        tier: str = "raw",
        lifecycle: str = "active",
        expires_at: str | None = None,
        retrieval_enabled: bool | None = None,
    ) -> MemoryEvent:
        _validate_id("trace id", trace_id)
        with self._lock:
            # RLock only serializes threads in one Worker. The sidecar lock
            # serializes append/read access across API and Worker processes.
            with self._trace_lock(trace_id, exclusive=True):
                events = self._read_unlocked(trace_id)
                event = MemoryEvent(
                    id=f"mem-{uuid4().hex[:12]}",
                    trace_id=trace_id,
                    sequence=len(events) + 1,
                    role=role,
                    content=_clip(content),
                    event_type=event_type,
                    work_item_id=work_item_id,
                    agent_id=agent_id,
                    attempt=attempt,
                    tool_name=tool_name,
                    source_refs=source_refs,
                    metadata=metadata or {},
                    created_at=_now(),
                    tier=tier,
                    lifecycle=lifecycle,
                    expires_at=expires_at,
                    retrieval_enabled=(
                        tier != "temporary"
                        and _default_retrieval_enabled(role, event_type)
                        if retrieval_enabled is None
                        else retrieval_enabled
                    ),
                )
                path = self._path(trace_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                    # A checkpoint/event is only acknowledged after its bytes
                    # reach the OS, so a killed Worker cannot acknowledge a
                    # record that was never durable.
                    try:
                        os.fsync(stream.fileno())
                    except OSError:
                        pass
                self._index.sync((event,))
                return event

    def events(
        self,
        trace_id: str,
        *,
        work_item_id: str | None = None,
        roles: tuple[str, ...] = (),
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[MemoryEvent, ...]:
        all_events = self._read(trace_id)
        self._index.sync(all_events)
        selected = [
            event
            for event in all_events
            if event.sequence > after_sequence
            and (work_item_id is None or event.work_item_id == work_item_id)
            and (not roles or event.role in roles)
        ]
        return tuple(selected[-max(1, min(limit, 500)) :])

    def search(
        self,
        query: str,
        *,
        trace_id: str,
        work_item_id: str | None = None,
        tiers: tuple[str, ...] = ("working", "episodic", "durable", "raw"),
        roles: tuple[str, ...] = ("user", "planner", "assistant", "tool", "control"),
        limit: int = 20,
    ) -> tuple[MemoryEvent, ...]:
        """在当前 Trace 内进行可重建的关键词召回。

        这是后续向量 RAG 的替换边界。候选结果仍回到 JSONL 校验，索引不提供事实权威。
        """
        all_events = self._read(trace_id)
        self._index.sync(all_events)
        lexical_ids = self._index.search(
            query,
            trace_id=trace_id,
            work_item_id=work_item_id,
            limit=limit * 3,
        )
        vector_hits: tuple[tuple[str, float], ...] = ()
        if self._vector is not None:
            try:
                self._vector.sync(all_events)
                vector_hits = self._vector.search(
                    query,
                    trace_id=trace_id,
                    work_item_id=work_item_id,
                    limit=limit * 3,
                )
            except Exception:
                # 向量 provider 是增强能力，异常时不影响基础 FTS/顺序记忆。
                vector_hits = ()
        by_id = {event.id: event for event in all_events}
        suppressed = self._suppressed_ids(all_events)
        lexical_rank = {
            event_id: 1.0 / (rank + 1)
            for rank, event_id in enumerate(lexical_ids)
        }
        vector_score = {event_id: max(0.0, score) for event_id, score in vector_hits}
        ranked_ids = sorted(
            set(lexical_rank) | set(vector_score),
            key=lambda event_id: (
                0.65 * lexical_rank.get(event_id, 0.0)
                + 0.35 * vector_score.get(event_id, 0.0),
                by_id.get(event_id).sequence if by_id.get(event_id) else 0,
            ),
            reverse=True,
        )
        selected: list[MemoryEvent] = []
        for event_id in ranked_ids:
            event = by_id.get(event_id)
            if event is None or event.id in suppressed:
                continue
            if event.tier not in tiers or event.role not in roles:
                continue
            if not event.retrieval_enabled or event.lifecycle != "active":
                continue
            if event.expires_at is not None and event.expires_at <= _now():
                continue
            selected.append(event)
            if len(selected) >= max(1, min(limit, 100)):
                break
        return tuple(selected)

    def search_durable(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> tuple[MemoryEvent, ...]:
        """召回项目内已提升的 durable Memory，不读取普通跨 Trace 对话。"""
        all_events = self._all_events()
        self._index.sync(all_events)
        lexical_ids = self._index.search_project(query, limit=limit * 3)
        vector_hits: tuple[tuple[str, float], ...] = ()
        if self._vector is not None:
            try:
                self._vector.sync(all_events)
                vector_hits = self._vector.search(query, trace_id=None, limit=limit * 3)
            except Exception:
                vector_hits = ()
        by_id = {event.id: event for event in all_events}
        suppressed = self._suppressed_ids(all_events)
        lexical_rank = {event_id: 1.0 / (rank + 1) for rank, event_id in enumerate(lexical_ids)}
        vector_score = {event_id: max(0.0, score) for event_id, score in vector_hits}
        ranked_ids = sorted(
            set(lexical_rank) | set(vector_score),
            key=lambda event_id: (
                0.65 * lexical_rank.get(event_id, 0.0)
                + 0.35 * vector_score.get(event_id, 0.0),
                by_id.get(event_id).sequence if by_id.get(event_id) else 0,
            ),
            reverse=True,
        )
        selected: list[MemoryEvent] = []
        for event_id in ranked_ids:
            event = by_id.get(event_id)
            if event is None or event.id in suppressed or event.tier != "durable":
                continue
            if not _is_retrievable(event):
                continue
            selected.append(event)
            if len(selected) >= max(1, min(limit, 100)):
                break
        return tuple(selected)

    def view(
        self,
        trace_id: str,
        *,
        work_item_id: str | None = None,
        roles: tuple[str, ...] = (),
        limit: int = 12,
        max_chars: int = 12_000,
        checkpoint: dict[str, object] | None = None,
    ) -> MemoryView:
        all_events = self._read(trace_id)
        self._index.sync(all_events)
        suppressed = self._suppressed_ids(all_events)
        selected = [
            event
            for event in all_events
            if event.id not in suppressed
            and _is_retrievable(event)
            and (work_item_id is None or event.work_item_id == work_item_id)
            and (not roles or event.role in roles)
        ][-max(1, min(limit, 500)) :]
        checkpoint_event = next(
            (event for event in reversed(all_events) if event.event_type == "checkpoint"),
            None,
        )
        latest_checkpoint = None
        if checkpoint_event is not None:
            try:
                parsed = json.loads(checkpoint_event.content)
                if isinstance(parsed, dict):
                    latest_checkpoint = parsed
            except json.JSONDecodeError:
                latest_checkpoint = None
        events = [event for event in selected if event.event_type != "checkpoint"]
        while events and sum(len(event.content) for event in events) > max_chars:
            events.pop(0)
        return MemoryView(
            trace_id=trace_id,
            work_item_id=work_item_id,
            events=tuple(events),
            checkpoint=checkpoint if checkpoint is not None else latest_checkpoint,
        )

    def checkpoint(self, trace_id: str, *, state: dict[str, object]) -> MemoryEvent:
        return self.append(
            trace_id=trace_id,
            role="control",
            event_type="checkpoint",
            content=json.dumps(state, ensure_ascii=False, sort_keys=True),
            metadata={"checkpoint": True},
            tier="working",
            retrieval_enabled=False,
        )

    def propose_durable(
        self,
        *,
        trace_id: str,
        content: str,
        source_refs: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
    ) -> MemoryEvent:
        """创建长期记忆候选；候选默认不可检索，需控制面显式提升。"""
        return self.append(
            trace_id=trace_id,
            role="control",
            event_type="memory_candidate",
            content=content,
            source_refs=source_refs,
            metadata=metadata,
            tier="durable",
            lifecycle="candidate",
            retrieval_enabled=False,
        )

    def promote(self, event_id: str, *, trace_id: str) -> MemoryEvent:
        source = next((event for event in self._read(trace_id) if event.id == event_id), None)
        if source is None:
            raise KeyError(f"MemoryEvent 不存在: {event_id}")
        if source.lifecycle != "candidate":
            raise ValueError("只有 candidate MemoryEvent 可以被提升")
        return self.append(
            trace_id=trace_id,
            role="control",
            event_type="memory_promoted",
            content=source.content,
            source_refs=(source.id,) + source.source_refs,
            metadata={"promoted_from": source.id},
            tier="durable",
            lifecycle="active",
            retrieval_enabled=True,
        )

    def durable_candidates(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryEvent, ...]:
        """返回仍等待控制面审批的 durable 候选。

        候选不会因为 ``propose_durable`` 自动变成可检索记忆；已经批准、过期或
        被替代的候选也不会再次出现在审批队列中。原始事件仍保留，便于审计。
        """
        all_events = self._all_events() if trace_id is None else self._read(trace_id)
        suppressed = self._suppressed_ids(all_events)
        promoted = {
            str(event.metadata["promoted_from"])
            for event in all_events
            if event.event_type == "memory_promoted"
            and event.metadata.get("promoted_from")
        }
        candidates = [
            event
            for event in all_events
            if event.tier == "durable"
            and event.lifecycle == "candidate"
            and event.id not in suppressed
            and event.id not in promoted
            and (event.expires_at is None or event.expires_at > _now())
        ]
        return tuple(candidates[-max(1, min(limit, 500)) :])

    def cleanup_expired(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryEvent, ...]:
        """为已过期事件追加审计状态，保持 JSONL 事件不可变。

        这是幂等的：同一事件只会生成一次 ``memory_expired`` 状态。默认只处理
        当前项目内的事件，可用 ``trace_id`` 限定单次运行。
        """
        all_events = self._all_events() if trace_id is None else self._read(trace_id)
        now = _now()
        suppressed = self._suppressed_ids(all_events)
        expired = [
            event
            for event in all_events
            if event.id not in suppressed
            and event.lifecycle == "active"
            and event.expires_at is not None
            and event.expires_at <= now
        ]
        results: list[MemoryEvent] = []
        for event in expired[: max(1, min(limit, 500))]:
            results.append(self.expire(event.id, trace_id=event.trace_id))
        return tuple(results)

    def expire(self, event_id: str, *, trace_id: str) -> MemoryEvent:
        return self._state_event(trace_id, event_id, "memory_expired", "expired")

    def supersede(self, event_id: str, *, trace_id: str) -> MemoryEvent:
        return self._state_event(trace_id, event_id, "memory_superseded", "superseded")

    def summarize_trace(
        self,
        trace_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> MemoryEvent:
        """生成可追溯的运行摘要，并替代同一 Trace 的旧摘要。

        摘要是控制面根据事件生成的确定性压缩，不是新的事实来源；每个片段都保留
        `source_refs`，后续可以替换为经过校验的 LLM 摘要器。
        """
        all_events = self._read(trace_id)
        suppressed = self._suppressed_ids(all_events)
        previous = [
            event
            for event in all_events
            if event.event_type == "run_summary"
            and event.lifecycle == "active"
            and event.id not in suppressed
        ]
        for event in previous:
            self.supersede(event.id, trace_id=trace_id)
        all_events = self._read(trace_id)
        relevant: list[MemoryEvent] = []
        goals = [event for event in all_events if event.event_type == "goal"]
        if goals:
            relevant.append(goals[0])
        latest_results: dict[str, MemoryEvent] = {}
        for event in all_events:
            if event.event_type == "work_item_result" and event.work_item_id:
                latest_results[event.work_item_id] = event
        relevant.extend(latest_results.values())
        relevant.extend(
            event
            for event in all_events
            if event.event_type in {"assistant_output", "tool_result"}
        )
        relevant = relevant[-16:]
        lines = [f"Trace 运行摘要：status={status}"]
        if error:
            lines.append(f"终止原因：{error}")
        for event in relevant:
            label = event.event_type
            if event.work_item_id:
                label += f"[{event.work_item_id}]"
            lines.append(f"- {label}: {event.content}")
        if len(lines) == 1:
            lines.append("- 本次运行没有可压缩的交互事件。")
        content = "\n".join(lines)
        source_refs = tuple(event.id for event in relevant)
        quality = validate_summary(
            content=content,
            status=status,
            source_refs=source_refs,
            available_event_ids={event.id for event in all_events},
        )
        if not quality.valid:
            raise ValueError("Trace 摘要未通过质量校验: " + "; ".join(quality.issues))
        return self.append(
            trace_id=trace_id,
            role="control",
            event_type="run_summary",
            content=content,
            source_refs=source_refs,
            metadata={
                "summary_version": 1,
                "status": status,
                "source_count": len(relevant),
            },
            tier="episodic",
            lifecycle="active",
            retrieval_enabled=True,
        )

    def latest_sequence(self, trace_id: str) -> int:
        events = self._read(trace_id)
        return events[-1].sequence if events else 0

    def _path(self, trace_id: str) -> Path:
        _validate_id("trace id", trace_id)
        return self._root / trace_id / "memory.jsonl"

    def _read(self, trace_id: str) -> list[MemoryEvent]:
        with self._trace_lock(trace_id, exclusive=False):
            return self._read_unlocked(trace_id)

    def _read_unlocked(self, trace_id: str) -> list[MemoryEvent]:
        path = self._path(trace_id)
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events: list[MemoryEvent] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # A process can be killed between write() and the newline.
                # Under the trace lock this can only be a stale final record;
                # ignore that incomplete tail and keep all prior events usable.
                if index == len(lines) - 1:
                    break
                raise
            events.append(MemoryEvent.from_dict(payload))
        return events

    @contextmanager
    def _trace_lock(self, trace_id: str, *, exclusive: bool):
        """Coordinate JSONL reads and writes across independent Workers."""
        path = self._path(trace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name("memory.jsonl.lock")
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(handle.fileno(), operation)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _all_events(self) -> list[MemoryEvent]:
        events: list[MemoryEvent] = []
        if not self._root.is_dir():
            return events
        for path in sorted(self._root.glob("*/memory.jsonl")):
            trace_id = path.parent.name
            events.extend(self._read(trace_id))
        return events

    def _state_event(
        self, trace_id: str, event_id: str, event_type: str, lifecycle: str
    ) -> MemoryEvent:
        if not any(event.id == event_id for event in self._read(trace_id)):
            raise KeyError(f"MemoryEvent 不存在: {event_id}")
        return self.append(
            trace_id=trace_id,
            role="control",
            event_type=event_type,
            content=f"MemoryEvent {event_id} -> {lifecycle}",
            metadata={"target_event_id": event_id},
            tier="raw",
            lifecycle="active",
            retrieval_enabled=False,
        )

    @staticmethod
    def _suppressed_ids(events: list[MemoryEvent]) -> set[str]:
        return {
            str(event.metadata["target_event_id"])
            for event in events
            if event.event_type in {"memory_expired", "memory_superseded"}
            and event.metadata.get("target_event_id")
        }


def _clip(content: str) -> str:
    value = str(content).strip()
    if len(value) <= _MAX_CONTENT:
        return value
    return value[: _MAX_CONTENT - 40] + "\n[内容已截断，完整内容见 Trace/Evidence]"


def _validate_id(name: str, value: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} 必须是受限 ID")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_retrieval_enabled(role: str, event_type: str) -> bool:
    return not (
        role == "system"
        or event_type in {"checkpoint", "agent_input", "planner_input", "planner_repair_input"}
    )


def _is_retrievable(event: MemoryEvent) -> bool:
    return (
        event.retrieval_enabled
        and event.lifecycle == "active"
        and (event.expires_at is None or event.expires_at > _now())
        and event.event_type != "checkpoint"
    )

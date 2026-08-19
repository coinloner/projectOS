"""可重建的 Memory SQLite/FTS5 索引。

索引不是 Memory 的事实来源；原始事件仍保存在 JSONL 中。删除索引后，
``MemoryStore`` 可以从事件日志重新同步它。
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
import re
import sqlite3
from pathlib import Path

from app.memory.store_types import IndexedMemoryEvent


_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class MemoryIndex:
    """每个项目一个 SQLite 索引，使用 FTS5 做精确/关键词召回。"""

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / ".projectos" / "memory" / "index.sqlite3"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    work_item_id TEXT,
                    role TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    retrieval_enabled INTEGER NOT NULL,
                    expires_at TEXT,
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_trace_sequence
                    ON memory_events(trace_id, sequence DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_work_item
                    ON memory_events(trace_id, work_item_id, sequence DESC);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_events_fts USING fts5(
                    event_id UNINDEXED,
                    trace_id UNINDEXED,
                    content,
                    tokenize='unicode61'
                );
                """
            )

    def sync(self, events: Iterable[IndexedMemoryEvent]) -> None:
        rows = tuple(events)
        if not rows:
            return
        with self._connection() as connection:
            for event in rows:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO memory_events (
                        event_id, trace_id, sequence, work_item_id, role,
                        event_type, tier, lifecycle, retrieval_enabled,
                        expires_at, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.trace_id,
                        event.sequence,
                        event.work_item_id,
                        event.role,
                        event.event_type,
                        event.tier,
                        event.lifecycle,
                        int(event.retrieval_enabled),
                        event.expires_at,
                        event.content,
                    ),
                )
                connection.execute(
                    "DELETE FROM memory_events_fts WHERE event_id = ?",
                    (event.id,),
                )
                connection.execute(
                    "INSERT INTO memory_events_fts(event_id, trace_id, content) VALUES (?, ?, ?)",
                    (event.id, event.trace_id, event.content),
                )

    def search(
        self,
        query: str,
        *,
        trace_id: str,
        work_item_id: str | None = None,
        limit: int = 20,
    ) -> tuple[str, ...]:
        match = _fts_query(query)
        if not match:
            return ()
        clauses = [
            "f.trace_id = ?",
            "e.retrieval_enabled = 1",
            "e.lifecycle = 'active'",
            "(e.expires_at IS NULL OR e.expires_at > datetime('now'))",
        ]
        parameters: list[object] = [match, trace_id]
        if work_item_id is not None:
            clauses.append("e.work_item_id = ?")
            parameters.append(work_item_id)
        parameters.append(max(1, min(limit, 100)))
        rows = self._query(
            """
            SELECT f.event_id
            FROM memory_events_fts AS f
            JOIN memory_events AS e ON e.event_id = f.event_id
            WHERE memory_events_fts MATCH ? AND """
            + " AND ".join(clauses)
            + " ORDER BY bm25(memory_events_fts), e.sequence DESC LIMIT ?",
            parameters,
        )
        if not rows:
            # unicode61 对连续中文的切分依赖 SQLite 版本；保留精确子串回退，
            # 确保中文、路径和数字不会因为 tokenizer 差异完全失去召回。
            rows = self._query(
                """
                SELECT e.event_id
                FROM memory_events AS e
                WHERE e.trace_id = ?
                  AND e.retrieval_enabled = 1
                  AND e.lifecycle = 'active'
                  AND (e.expires_at IS NULL OR e.expires_at > datetime('now'))
                  AND e.content LIKE ?
                ORDER BY e.sequence DESC LIMIT ?
                """,
                [trace_id, f"%{query.strip()}%", max(1, min(limit, 100))],
            )
        return tuple(str(row[0]) for row in rows)

    def search_project(self, query: str, *, limit: int = 20) -> tuple[str, ...]:
        """在当前项目的所有 Trace 中召回候选 ID；调用方仍需按 tier/生命周期校验。"""
        match = _fts_query(query)
        if not match:
            return ()
        rows = self._query(
            """
            SELECT f.event_id
            FROM memory_events_fts AS f
            JOIN memory_events AS e ON e.event_id = f.event_id
            WHERE memory_events_fts MATCH ?
              AND e.tier = 'durable'
              AND e.retrieval_enabled = 1
              AND e.lifecycle = 'active'
              AND (e.expires_at IS NULL OR e.expires_at > datetime('now'))
            ORDER BY bm25(memory_events_fts), e.sequence DESC LIMIT ?
            """,
            [match, max(1, min(limit, 100))],
        )
        if not rows:
            rows = self._query(
                """
                SELECT event_id
                FROM memory_events
                WHERE tier = 'durable'
                  AND retrieval_enabled = 1
                  AND lifecycle = 'active'
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                  AND content LIKE ?
                ORDER BY sequence DESC LIMIT ?
                """,
                [f"%{query.strip()}%", max(1, min(limit, 100))],
            )
        return tuple(str(row[0]) for row in rows)

    def _query(self, statement: str, parameters: list[object]) -> list[tuple[object, ...]]:
        with self._connection() as connection:
            return [tuple(row) for row in connection.execute(statement, parameters)]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _fts_query(query: str) -> str:
    tokens = _TOKEN.findall(query.strip())
    if not tokens:
        return ""
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:24])

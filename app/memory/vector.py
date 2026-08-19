"""可插拔的 Memory 向量索引。

ProjectOS 不内置 embedding 模型。调用方提供 ``EmbeddingProvider`` 后，
向量索引才会启用；模型、维度和供应商都不会进入 Memory 事实模型。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> Sequence[float]:
        """将文本转换为固定维度向量。"""


class MemoryVectorIndex:
    """使用 SQLite 保存向量，余弦相似度在进程内计算。"""

    def __init__(self, project_path: str, provider: EmbeddingProvider) -> None:
        self._path = Path(project_path) / ".projectos" / "memory" / "index.sqlite3"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._provider = provider
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_vectors (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    work_item_id TEXT,
                    dimensions INTEGER NOT NULL,
                    vector_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_vectors_scope "
                "ON memory_vectors(trace_id, work_item_id)"
            )

    def sync(self, events: Iterable[object]) -> None:
        rows = tuple(events)
        if not rows:
            return
        with self._connection() as connection:
            for event in rows:
                if not getattr(event, "retrieval_enabled", False):
                    continue
                if getattr(event, "lifecycle", "active") != "active":
                    continue
                vector = _normalise(self._provider.embed(event.content))
                if not vector:
                    continue
                connection.execute(
                    """
                    INSERT OR REPLACE INTO memory_vectors(
                        event_id, trace_id, work_item_id, dimensions, vector_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.trace_id,
                        event.work_item_id,
                        len(vector),
                        json.dumps(vector),
                    ),
                )

    def search(
        self,
        query: str,
        *,
        trace_id: str | None,
        work_item_id: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[str, float], ...]:
        query_vector = _normalise(self._provider.embed(query))
        if not query_vector:
            return ()
        clauses = ["dimensions = ?"]
        parameters: list[object] = [len(query_vector)]
        if trace_id is not None:
            clauses.insert(0, "trace_id = ?")
            parameters.insert(0, trace_id)
        if work_item_id is not None:
            clauses.append("work_item_id = ?")
            parameters.append(work_item_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_id, vector_json FROM memory_vectors WHERE "
                + " AND ".join(clauses),
                parameters,
            )
            scored = [
                (str(event_id), _cosine(query_vector, json.loads(vector_json)))
                for event_id, vector_json in rows
            ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return tuple(scored[: max(1, min(limit, 100))])

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self._path, timeout=30)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _normalise(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not math.isfinite(value) for value in vector):
        return ()
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return ()
    return tuple(value / magnitude for value in vector)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True))

"""Deterministic recursive Architecture control primitives for scheme D.

The model proposes a split or leaf; the control plane owns the decision, validates
leaf closure, and persists a snapshot that can be resumed without redesigning
accepted ancestors.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

Boundary = Literal["split", "leaf"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RecursiveNode:
    node_id: str
    parent_id: str | None
    objective: str
    boundary: Boundary
    provided_interfaces: tuple[str, ...] = ()
    consumed_interfaces: tuple[str, ...] = ()
    owned_files: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    children: tuple[str, ...] = ()
    input_digest: str = ""
    artifact_ref: str | None = None
    status: str = "accepted"

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.objective.strip():
            raise ValueError("recursive node identity and objective are required")
        if self.boundary == "leaf":
            if not 1 <= len(self.owned_files) <= 3:
                raise ValueError("recursive leaf must own 1-3 concrete files")
            if not self.acceptance:
                raise ValueError("recursive leaf must have acceptance criteria")
        elif not self.children:
            raise ValueError("recursive split must have children")
        if len(set(self.owned_files)) != len(self.owned_files):
            raise ValueError("recursive node owned_files must be unique")

    @property
    def is_leaf(self) -> bool:
        return self.boundary == "leaf"


@dataclass(frozen=True)
class RecursiveSnapshot:
    schema_version: int
    protocol_version: str
    trace_id: str
    plan_id: str
    requirement_ref: str
    requirement_digest: str
    blueprint_ref: str
    blueprint_digest: str
    accepted_nodes: tuple[RecursiveNode, ...] = ()
    current_node_ids: tuple[str, ...] = ()
    invalidated_node_ids: tuple[str, ...] = ()
    updated_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "trace_id": self.trace_id,
            "plan_id": self.plan_id,
            "requirement_ref": self.requirement_ref,
            "requirement_digest": self.requirement_digest,
            "blueprint_ref": self.blueprint_ref,
            "blueprint_digest": self.blueprint_digest,
            "accepted_nodes": [asdict(node) for node in self.accepted_nodes],
            "current_node_ids": list(self.current_node_ids),
            "invalidated_node_ids": list(self.invalidated_node_ids),
            "updated_at": self.updated_at,
        }

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())


class RecursiveSnapshotStore:
    protocol_version = "architecture-d/v1"
    relative_path = ".projectos/architecture/recursive-snapshot.json"

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / self.relative_path

    def save(self, snapshot: RecursiveSnapshot) -> None:
        if snapshot.protocol_version != self.protocol_version:
            raise ValueError("unsupported recursive protocol version")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as tmp:
            json.dump(snapshot.as_dict(), tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            temporary = tmp.name
        os.replace(temporary, self._path)

    def load(self) -> RecursiveSnapshot:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if raw.get("protocol_version") != self.protocol_version:
            raise ValueError("recursive snapshot protocol version is stale")
        nodes = tuple(RecursiveNode(**item) for item in raw.get("accepted_nodes", ()))
        return RecursiveSnapshot(
            schema_version=int(raw["schema_version"]),
            protocol_version=str(raw["protocol_version"]),
            trace_id=str(raw["trace_id"]),
            plan_id=str(raw["plan_id"]),
            requirement_ref=str(raw["requirement_ref"]),
            requirement_digest=str(raw["requirement_digest"]),
            blueprint_ref=str(raw["blueprint_ref"]),
            blueprint_digest=str(raw["blueprint_digest"]),
            accepted_nodes=nodes,
            current_node_ids=tuple(raw.get("current_node_ids", ())),
            invalidated_node_ids=tuple(raw.get("invalidated_node_ids", ())),
            updated_at=str(raw.get("updated_at", "")),
        )


def validate_leaf(node: RecursiveNode, *, parent_input_digest: str, occupied_files: set[str]) -> None:
    if not node.is_leaf:
        raise ValueError("only leaf nodes can be compiled")
    if node.input_digest != parent_input_digest:
        raise ValueError("leaf input digest does not match frozen parent input")
    overlap = occupied_files.intersection(node.owned_files)
    if overlap:
        raise ValueError("leaf ownership overlaps accepted sibling: " + ", ".join(sorted(overlap)))
    if not node.provided_interfaces and not node.consumed_interfaces:
        raise ValueError("leaf must declare an interface closure or explicit boundary")

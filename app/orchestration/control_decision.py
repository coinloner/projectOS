"""Durable control-plane decisions for cross-node recovery."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json, os
from pathlib import Path
from tempfile import NamedTemporaryFile

ACTIONS = frozenset({
    "retry_current_work_item", "rebuild_architecture_subgraph",
    "retry_upstream_stage", "await_user_input", "terminate_run",
})

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class ControlDecision:
    decision_id: str
    trace_id: str
    plan_id: str
    plan_revision: int
    source_work_item_id: str
    source_failure_digest: str
    action: str
    target_work_item_ids: tuple[str, ...] = ()
    invalidated_artifact_refs: tuple[str, ...] = ()
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unsupported control decision action: {self.action}")
        if not self.decision_id.strip() or not self.trace_id.strip() or not self.source_work_item_id.strip():
            raise ValueError("control decision identity fields are required")

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("target_work_item_ids", "invalidated_artifact_refs", "evidence_refs"):
            payload[key] = list(payload[key])
        return payload

class ControlDecisionStore:
    relative_path = ".projectos/control/control-decisions.jsonl"
    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / self.relative_path
    def append(self, decision: ControlDecision) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("a", encoding="utf-8", dir=self._path.parent, delete=False) as tmp:
            tmp.write(json.dumps(decision.as_dict(), ensure_ascii=False) + "\n")
            tmp.flush(); os.fsync(tmp.fileno())
            temp = tmp.name
        with self._path.open("a", encoding="utf-8") as target, open(temp, encoding="utf-8") as source:
            target.write(source.read()); target.flush(); os.fsync(target.fileno())
        os.unlink(temp)
    def list(self) -> tuple[ControlDecision, ...]:
        if not self._path.exists(): return ()
        result=[]
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            raw=json.loads(line)
            result.append(ControlDecision(
                **{k: tuple(v) if k in {"target_work_item_ids","invalidated_artifact_refs","evidence_refs"} else v for k,v in raw.items()}
            ))
        return tuple(result)

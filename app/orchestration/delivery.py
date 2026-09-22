"""项目级交付状态和需求追踪的确定性控制模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from threading import RLock
from typing import Iterable


class DeliveryState(str, Enum):
    PLANNED = "planned"
    IMPLEMENTING = "implementing"
    BUILT = "built"
    RUNNABLE = "runnable"
    BEHAVIOR_VERIFIED = "behavior_verified"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    NEEDS_REWORK = "needs_rework"
    DELIVERY_READY = "delivery_ready"
    BLOCKED = "blocked"
    FAILED = "failed"


_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    DeliveryState.PLANNED: frozenset({DeliveryState.IMPLEMENTING, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.IMPLEMENTING: frozenset({DeliveryState.BUILT, DeliveryState.NEEDS_REWORK, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.BUILT: frozenset({DeliveryState.RUNNABLE, DeliveryState.NEEDS_REWORK, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.RUNNABLE: frozenset({DeliveryState.BEHAVIOR_VERIFIED, DeliveryState.NEEDS_REWORK, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.BEHAVIOR_VERIFIED: frozenset({DeliveryState.DELIVERY_READY, DeliveryState.NEEDS_REWORK, DeliveryState.BLOCKED}),
    DeliveryState.WAITING_FOR_APPROVAL: frozenset({DeliveryState.IMPLEMENTING, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.NEEDS_REWORK: frozenset({DeliveryState.IMPLEMENTING, DeliveryState.BUILT, DeliveryState.BLOCKED, DeliveryState.FAILED}),
    DeliveryState.DELIVERY_READY: frozenset(),
    DeliveryState.BLOCKED: frozenset({DeliveryState.IMPLEMENTING, DeliveryState.FAILED}),
    DeliveryState.FAILED: frozenset({DeliveryState.IMPLEMENTING}),
}


def can_transition(current: DeliveryState, target: DeliveryState) -> bool:
    return target == current or target in _TRANSITIONS[current]


@dataclass(frozen=True)
class RequirementRecord:
    requirement_id: str
    text: str
    acceptance_criteria: tuple[str, ...] = ()
    implementation_units: tuple[str, ...] = ()
    test_evidence_ids: tuple[str, ...] = ()
    runtime_evidence_ids: tuple[str, ...] = ()
    actor: str | None = None
    action: str | None = None
    preconditions: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    error_cases: tuple[str, ...] = ()
    priority: str = "must"

    def as_dict(self) -> dict[str, object]:
        return {
            "requirement_id": self.requirement_id,
            "text": self.text,
            "acceptance_criteria": list(self.acceptance_criteria),
            "implementation_units": list(self.implementation_units),
            "test_evidence_ids": list(self.test_evidence_ids),
            "runtime_evidence_ids": list(self.runtime_evidence_ids),
            "actor": self.actor,
            "action": self.action,
            "preconditions": list(self.preconditions),
            "expected_outputs": list(self.expected_outputs),
            "error_cases": list(self.error_cases),
            "priority": self.priority,
        }


@dataclass
class TraceabilityMatrix:
    requirements: dict[str, RequirementRecord] = field(default_factory=dict)

    def register(self, record: RequirementRecord) -> None:
        if not record.requirement_id.strip():
            raise ValueError("requirement_id 不能为空")
        self.requirements[record.requirement_id] = record

    def bind_implementation(self, requirement_ids: Iterable[str], unit_id: str) -> None:
        for requirement_id in requirement_ids:
            record = self.requirements.get(requirement_id)
            if record is None:
                continue
            self.requirements[requirement_id] = RequirementRecord(
                **{**record.as_dict(), "acceptance_criteria": tuple(record.acceptance_criteria),
                   "implementation_units": tuple(dict.fromkeys((*record.implementation_units, unit_id))),
                   "test_evidence_ids": tuple(record.test_evidence_ids),
                   "runtime_evidence_ids": tuple(record.runtime_evidence_ids)}
            )

    def incomplete(self) -> tuple[str, ...]:
        return tuple(sorted(
            requirement_id for requirement_id, record in self.requirements.items()
            if not record.implementation_units or not record.test_evidence_ids or not record.runtime_evidence_ids
        ))

    def as_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "requirements": [record.as_dict() for record in self.requirements.values()]}

    def coverage_summary(self) -> dict[str, object]:
        total = len(self.requirements)
        implemented = sum(bool(item.implementation_units) for item in self.requirements.values())
        tested = sum(bool(item.test_evidence_ids) for item in self.requirements.values())
        runtime = sum(bool(item.runtime_evidence_ids) for item in self.requirements.values())
        return {
            "total": total,
            "implemented": implemented,
            "tested": tested,
            "runtime_verified": runtime,
            "implementation_rate": implemented / total if total else None,
            "test_rate": tested / total if total else None,
            "runtime_rate": runtime / total if total else None,
            "complete": not self.incomplete(),
        }


@dataclass(frozen=True)
class QualityMatrix:
    """项目级质量维度快照，规则由 Contract/Runtime 动态声明。"""

    dimensions: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "dimensions": dict(sorted(self.dimensions.items()))}


class DeliveryStore:
    """持久化项目级交付状态、需求追踪矩阵和状态变更事件。"""

    def __init__(self, project_path: str) -> None:
        self._root = Path(project_path).resolve() / ".projectos" / "delivery"
        self._lock = RLock()

    def state(self) -> DeliveryState:
        path = self._root / "state.json"
        if not path.is_file():
            return DeliveryState.PLANNED
        return DeliveryState(str(json.loads(path.read_text(encoding="utf-8")).get("state", "planned")))

    def transition(self, target: DeliveryState, *, reason: str = "", evidence_ids: tuple[str, ...] = ()) -> DeliveryState:
        with self._lock:
            current = self.state()
            if not can_transition(current, target):
                raise ValueError(f"非法交付状态转换: {current.value} -> {target.value}")
            self._root.mkdir(parents=True, exist_ok=True)
            payload = {"schema_version": 1, "state": target.value, "reason": reason, "evidence_ids": list(evidence_ids)}
            (self._root / "state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            event = {"from": current.value, "to": target.value, "reason": reason, "evidence_ids": list(evidence_ids)}
            with (self._root / "state-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return target

    def save_matrix(self, matrix: TraceabilityMatrix) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "traceability.json").write_text(json.dumps(matrix.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_matrix(self) -> TraceabilityMatrix:
        path = self._root / "traceability.json"
        matrix = TraceabilityMatrix()
        if not path.is_file():
            return matrix
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload.get("requirements", []):
            if isinstance(raw, dict):
                matrix.register(RequirementRecord(
                    requirement_id=str(raw["requirement_id"]),
                    text=str(raw.get("text", "")),
                    acceptance_criteria=tuple(str(value) for value in raw.get("acceptance_criteria", [])),
                    implementation_units=tuple(str(value) for value in raw.get("implementation_units", [])),
                    test_evidence_ids=tuple(str(value) for value in raw.get("test_evidence_ids", [])),
                    runtime_evidence_ids=tuple(str(value) for value in raw.get("runtime_evidence_ids", [])),
                    actor=str(raw["actor"]) if raw.get("actor") else None,
                    action=str(raw["action"]) if raw.get("action") else None,
                    preconditions=tuple(str(value) for value in raw.get("preconditions", [])),
                    expected_outputs=tuple(str(value) for value in raw.get("expected_outputs", [])),
                    error_cases=tuple(str(value) for value in raw.get("error_cases", [])),
                    priority=str(raw.get("priority", "must")),
                ))
        return matrix

    def save_quality_matrix(self, matrix: QualityMatrix) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "quality-matrix.json").write_text(
            json.dumps(matrix.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def load_quality_matrix(self) -> QualityMatrix:
        path = self._root / "quality-matrix.json"
        if not path.is_file():
            return QualityMatrix()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            dimensions = payload.get("dimensions", {}) if isinstance(payload, dict) else {}
            return QualityMatrix({str(key): str(value) for key, value in dimensions.items()})
        except (OSError, ValueError, TypeError):
            return QualityMatrix()

    def snapshot_quality(self, *, contract: object | None = None, runtime: object | None = None) -> QualityMatrix:
        """Derive applicable quality dimensions without inventing business rules."""
        dimensions: dict[str, str] = {}
        contract_tests = getattr(contract, "required_test_types", ()) if contract is not None else ()
        for test_type in contract_tests or ():
            dimensions[f"test:{test_type}"] = "required"
        if contract is not None:
            entrypoints = getattr(contract, "entrypoints", None)
            if entrypoints is not None and getattr(entrypoints, "backend_file", None):
                dimensions["runtime:backend_entrypoint"] = "required"
            if entrypoints is not None and getattr(entrypoints, "frontend_file", None):
                dimensions["runtime:frontend_entrypoint"] = "required"
        if runtime is not None:
            profile = getattr(runtime, "profile", None) or (runtime.get("profile") if isinstance(runtime, dict) else None)
            if profile:
                dimensions["runtime:profile"] = str(profile)
        matrix = QualityMatrix(dimensions)
        self.save_quality_matrix(matrix)
        return matrix

    def initialize_from_markdown(self, content: str) -> TraceabilityMatrix:
        """从需求中的 AC 编号建立稳定的初始追踪记录。"""
        import re

        matrix = self.load_matrix()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            # Accept ordinary Markdown list/heading/bold prefixes, while
            # avoiding references such as "覆盖 AC-1、AC-2" in prose.
            normalized = re.sub(r"^[\s#>*\-\d.)]+", "", line).strip("*_ ")
            match = re.match(
                r"(AC-\d+)\b(?:\s*[:：.\-)]+\s*|\s+)(.*)",
                normalized,
                re.IGNORECASE,
            )
            if not match:
                continue
            requirement_id = match.group(1).upper()
            text = match.group(2).strip().strip("*_ ") or normalized
            matrix.register(RequirementRecord(requirement_id, text, (requirement_id,)))
        self.save_matrix(matrix)
        return matrix

    def bind_plan(self, plan: object) -> TraceabilityMatrix:
        matrix = self.load_matrix()
        for item in getattr(plan, "work_items", ()):
            unit_id = item.implementation_unit_id or item.id
            matrix.bind_implementation(item.requirement_ids, unit_id)
        self.save_matrix(matrix)
        return matrix

    def bind_evidence(self, requirement_ids: Iterable[str], *, evidence_id: str, runtime: bool) -> None:
        matrix = self.load_matrix()
        for requirement_id in requirement_ids:
            record = matrix.requirements.get(requirement_id)
            if record is None:
                continue
            values = (record.runtime_evidence_ids if runtime else record.test_evidence_ids)
            updated = tuple(dict.fromkeys((*values, evidence_id)))
            matrix.requirements[requirement_id] = RequirementRecord(
                requirement_id=record.requirement_id,
                text=record.text,
                acceptance_criteria=record.acceptance_criteria,
                implementation_units=record.implementation_units,
                test_evidence_ids=record.test_evidence_ids if runtime else updated,
                runtime_evidence_ids=updated if runtime else record.runtime_evidence_ids,
            )
        self.save_matrix(matrix)

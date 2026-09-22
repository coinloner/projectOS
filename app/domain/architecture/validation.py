"""Durable validation receipts for accepted Architecture design artifacts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.artifact.repository import ArtifactRef


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ValidationReceipt:
    artifact_ref: ArtifactRef
    artifact_digest: str
    parent_refs: tuple[str, ...]
    parent_digests: tuple[str, ...]
    validator_version: str
    checks: tuple[str, ...]
    status: str = "passed"
    created_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "artifact_ref": asdict(self.artifact_ref),
            "artifact_digest": self.artifact_digest,
            "parent_refs": list(self.parent_refs),
            "parent_digests": list(self.parent_digests),
            "validator_version": self.validator_version,
            "checks": list(self.checks),
            "status": self.status,
            "created_at": self.created_at or _now(),
        }


class ValidationReceiptStore:
    relative_root = ".projectos/architecture/validation-receipts"

    def __init__(self, project_path: str) -> None:
        self._root = Path(project_path) / self.relative_root

    def _path(self, ref: ArtifactRef) -> Path:
        if ref.layer != "staged" or not ref.trace_id or not ref.work_item_id or not ref.slot:
            raise ValueError("ValidationReceipt 必须引用 staged Architecture artifact")
        return self._root / ref.trace_id / ref.work_item_id / f"{ref.slot}.json"

    def save(self, receipt: ValidationReceipt) -> None:
        path = self._path(receipt.artifact_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(receipt.as_dict(), ensure_ascii=False, indent=2) + "\n"
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            temporary = tmp.name
        os.replace(temporary, path)

    def load(self, ref: ArtifactRef) -> ValidationReceipt:
        path = self._path(ref)
        raw = json.loads(path.read_text(encoding="utf-8"))
        artifact = raw["artifact_ref"]
        parsed_ref = ArtifactRef(
            artifact_key=artifact["artifact_key"],
            layer=artifact["layer"],
            trace_id=artifact.get("trace_id"),
            work_item_id=artifact.get("work_item_id"),
            slot=artifact.get("slot"),
            revision_id=artifact.get("revision_id"),
        )
        return ValidationReceipt(
            artifact_ref=parsed_ref,
            artifact_digest=str(raw["artifact_digest"]),
            parent_refs=tuple(raw.get("parent_refs", ())),
            parent_digests=tuple(raw.get("parent_digests", ())),
            validator_version=str(raw["validator_version"]),
            checks=tuple(raw.get("checks", ())),
            status=str(raw.get("status", "passed")),
            created_at=str(raw.get("created_at", "")),
        )

    def exists(self, ref: ArtifactRef) -> bool:
        try:
            return self._path(ref).is_file()
        except ValueError:
            return False

    def verify(self, ref: ArtifactRef, *, artifact_digest: str, parent_digests: tuple[str, ...]) -> ValidationReceipt:
        receipt = self.load(ref)
        if receipt.status != "passed":
            raise ValueError(f"ValidationReceipt 状态不是 passed: {ref.ref_id}")
        if receipt.artifact_digest != artifact_digest:
            raise ValueError(f"ValidationReceipt artifact digest 不匹配: {ref.ref_id}")
        if receipt.parent_digests != tuple(parent_digests):
            raise ValueError(f"ValidationReceipt parent digest 不匹配: {ref.ref_id}")
        return receipt


def digest_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

"""跨 Worker 可恢复的能力授权记录。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4


@dataclass(frozen=True)
class CapabilityGrant:
    grant_id: str
    trace_id: str
    work_item_id: str | None
    capability: str
    source_name: str
    scope: str = "trace"
    approved_by: str = "api"
    approved_at: str = ""
    expires_at: str | None = None
    status: str = "active"

    def is_active(self, now: datetime | None = None) -> bool:
        if self.status != "active":
            return False
        if not self.expires_at:
            return True
        try:
            return datetime.fromisoformat(self.expires_at) > (now or datetime.now(timezone.utc))
        except ValueError:
            return False

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class CapabilityGrantStore:
    """授权账本。

    Trace 授权继续写入原有位置以兼容历史数据；project 授权写入项目级账本，
    因而可以被后续 Trace 读取。
    """

    def __init__(self, project_path: str, trace_id: str) -> None:
        self._project_root = Path(project_path).resolve() / ".projectos"
        self._trace_path = self._project_root / "runs" / trace_id / "capability-grants.json"
        self._project_path = self._project_root / "capability-grants.json"
        self._trace_id = trace_id
        self._lock = RLock()

    def list(self, *, include_inactive: bool = False) -> tuple[CapabilityGrant, ...]:
        with self._lock:
            raw = self._read_all()
            grants = tuple(
                grant
                for grant in (self._parse(item) for item in raw)
                if grant.scope == "project" or grant.trace_id == self._trace_id
            )
            active = tuple(grant for grant in grants if grant.is_active())
            if not include_inactive and len(active) != len(grants):
                self._write_all([grant.as_dict() for grant in grants if grant.is_active() or grant.status == "revoked"])
            return grants if include_inactive else active

    def grant(
        self,
        *,
        capability: str,
        source_name: str,
        work_item_id: str | None = None,
        scope: str = "trace",
        approved_by: str = "api",
        expires_at: str | None = None,
    ) -> CapabilityGrant:
        if scope not in {"node", "trace", "project"}:
            raise ValueError("能力授权 scope 必须是 node、trace 或 project")
        if not capability.strip() or not source_name.strip():
            raise ValueError("能力和来源不能为空")
        with self._lock:
            grants = list(self.list(include_inactive=True))
            for existing in grants:
                if (
                    existing.is_active()
                    and existing.capability == capability
                    and existing.source_name == source_name
                    and existing.work_item_id == work_item_id
                ):
                    return existing
            grant = CapabilityGrant(
                grant_id=f"grant-{uuid4().hex[:12]}",
                trace_id=self._trace_id,
                work_item_id=work_item_id,
                capability=capability.strip(),
                source_name=source_name.strip(),
                scope=scope,
                approved_by=approved_by,
                approved_at=datetime.now(timezone.utc).isoformat(),
                expires_at=expires_at,
            )
            if scope == "project":
                existing = [item for item in self._read_path(self._project_path)]
                self._write_path(self._project_path, existing + [grant.as_dict()])
            else:
                existing = [item for item in self._read_path(self._trace_path)]
                self._write_path(self._trace_path, [item for item in existing if item.get("status") == "active"] + [grant.as_dict()])
            return grant

    def revoke(self, grant_id: str) -> CapabilityGrant:
        with self._lock:
            grants = list(self.list(include_inactive=True))
            for index, grant in enumerate(grants):
                if grant.grant_id == grant_id:
                    revoked = CapabilityGrant(**{**grant.as_dict(), "status": "revoked"})
                    grants[index] = revoked
                    self._write_all([item.as_dict() for item in grants])
                    return revoked
        raise KeyError(f"能力授权不存在: {grant_id}")

    def _read_all(self) -> list[dict[str, object]]:
        items = self._read_path(self._project_path) + self._read_path(self._trace_path)
        seen: set[str] = set()
        result: list[dict[str, object]] = []
        for item in items:
            grant_id = str(item.get("grant_id", ""))
            if grant_id and grant_id in seen:
                continue
            if grant_id:
                seen.add(grant_id)
            result.append(item)
        return result

    def _write_all(self, grants: list[dict[str, object]]) -> None:
        project = [item for item in grants if item.get("scope") == "project"]
        trace = [item for item in grants if item.get("scope") != "project"]
        if project or self._project_path.is_file():
            self._write_path(self._project_path, project)
        if trace or self._trace_path.is_file():
            self._write_path(self._trace_path, trace)

    @staticmethod
    def _read_path(path: Path) -> list[dict[str, object]]:
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _write_path(path: Path, grants: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(grants, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _parse(raw: dict[str, object]) -> CapabilityGrant:
        return CapabilityGrant(
            grant_id=str(raw["grant_id"]), trace_id=str(raw["trace_id"]),
            work_item_id=str(raw["work_item_id"]) if raw.get("work_item_id") else None,
            capability=str(raw["capability"]), source_name=str(raw["source_name"]),
            scope=str(raw.get("scope", "trace")), approved_by=str(raw.get("approved_by", "api")),
            approved_at=str(raw.get("approved_at", "")),
            expires_at=str(raw["expires_at"]) if raw.get("expires_at") else None,
            status=str(raw.get("status", "active")),
        )

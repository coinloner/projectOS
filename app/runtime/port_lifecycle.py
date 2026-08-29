"""跨进程、可恢复的 host 端口租约生命周期管理。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator
from uuid import uuid4

from app.runtime.port_allocator import PortAllocator


@dataclass(frozen=True)
class PortLease:
    lease_id: str
    owner_id: str
    ports: tuple[int, ...]
    pid: int
    created_at: str
    expires_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "owner_id": self.owner_id,
            "ports": list(self.ports),
            "pid": self.pid,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class PortLifecycleManager:
    """为应用运行分配、续租、释放并回收端口。

    registry 是跨进程共享的事实来源；真实 bind 检查仍由 PortAllocator 执行。
    进程异常退出后，下一次 acquire 会按 PID/TTL 清理陈旧租约。
    """

    def __init__(
        self,
        registry_path: str | None = None,
        *,
        lease_ttl_seconds: int = 3600,
    ) -> None:
        if lease_ttl_seconds < 30:
            raise ValueError("lease_ttl_seconds 至少为 30 秒")
        self._path = Path(registry_path or (Path(tempfile.gettempdir()) / "projectos-port-leases.json"))
        self._ttl = timedelta(seconds=lease_ttl_seconds)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def registry_path(self) -> str:
        return str(self._path)

    def acquire(
        self,
        owner_id: str,
        count: int,
        *,
        allocator: PortAllocator,
    ) -> PortLease:
        if not owner_id or not owner_id.strip():
            raise ValueError("owner_id 不能为空")
        now = datetime.now(timezone.utc)
        with self._locked_state() as state:
            leases = self._reclaim_state(state, now)
            held = {port for lease in leases for port in lease.ports}
            ports = tuple(allocator.allocate(count, excluded_ports=held))
            lease = PortLease(
                lease_id=f"lease-{uuid4().hex[:16]}",
                owner_id=owner_id,
                ports=ports,
                pid=os.getpid(),
                created_at=now.isoformat(),
                expires_at=(now + self._ttl).isoformat(),
            )
            state[lease.lease_id] = lease.as_dict()
            return lease

    def renew(self, lease_id: str) -> PortLease:
        now = datetime.now(timezone.utc)
        with self._locked_state() as state:
            leases = self._reclaim_state(state, now)
            raw = state.get(lease_id)
            if raw is None:
                raise KeyError(f"端口租约不存在或已过期: {lease_id}")
            lease = PortLease(
                lease_id=lease_id,
                owner_id=str(raw["owner_id"]),
                ports=tuple(int(port) for port in raw["ports"]),
                pid=int(raw["pid"]),
                created_at=str(raw["created_at"]),
                expires_at=(now + self._ttl).isoformat(),
            )
            state[lease_id] = lease.as_dict()
            return lease

    def release(self, lease_id: str, *, allocator: PortAllocator) -> tuple[int, ...]:
        with self._locked_state() as state:
            raw = state.pop(lease_id, None)
            if raw is None:
                return ()
            ports = tuple(int(port) for port in raw["ports"])
            allocator.release(ports)
            return ports

    def active_leases(self) -> tuple[PortLease, ...]:
        with self._locked_state() as state:
            leases = self._reclaim_state(state, datetime.now(timezone.utc))
            return tuple(leases)

    def reclaim(self) -> tuple[str, ...]:
        with self._locked_state() as state:
            before = set(state)
            self._reclaim_state(state, datetime.now(timezone.utc))
            return tuple(sorted(before - set(state)))

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, dict[str, object]]]:
        self._path.touch(exist_ok=True)
        with self._path.open("r+", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - Windows fallback uses atomic file writes.
                pass
            try:
                handle.seek(0)
                try:
                    state = json.load(handle)
                except json.JSONDecodeError:
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                yield state
                handle.seek(0)
                handle.truncate()
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
            finally:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except ImportError:  # pragma: no cover
                    pass

    def _reclaim_state(
        self,
        state: dict[str, dict[str, object]],
        now: datetime,
    ) -> list[PortLease]:
        active: list[PortLease] = []
        for lease_id, raw in list(state.items()):
            try:
                expires = datetime.fromisoformat(str(raw["expires_at"]))
                pid = int(raw["pid"])
                ports = tuple(int(port) for port in raw["ports"])
                if expires <= now or not _process_alive(pid):
                    del state[lease_id]
                    continue
                active.append(
                    PortLease(
                        lease_id=lease_id,
                        owner_id=str(raw["owner_id"]),
                        ports=ports,
                        pid=pid,
                        created_at=str(raw["created_at"]),
                        expires_at=str(raw["expires_at"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                del state[lease_id]
        return active


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

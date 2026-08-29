"""受信应用的 host 端口统一分配器。"""

from __future__ import annotations

from collections.abc import Iterable
import errno
import os
import socket
from threading import Lock


class PortAllocationError(RuntimeError):
    """端口池中没有足够的可用端口。"""


class PortAllocator:
    """在受限端口池内分配 host 端口，运行结束时统一归还。

    分配前对每个候选端口做真实 bind 检查，避免与池外的本机进程
    （例如用户自行启动的服务）冲突；池内已分配端口由内部记录互斥。
    """

    DEFAULT_RANGE = (8100, 8299)

    def __init__(
        self,
        *,
        port_range: tuple[int, int] = DEFAULT_RANGE,
        bind_host: str = "127.0.0.1",
        probe_mode: str | None = None,
    ) -> None:
        start, end = port_range
        if start < 1 or end < start or end > 65535:
            raise ValueError(f"端口范围无效: {port_range}")
        self._start, self._end = start, end
        self._bind_host = bind_host
        self._probe_mode = probe_mode or os.environ.get("PROJECTOS_PORT_PROBE_MODE", "auto")
        if self._probe_mode not in {"auto", "strict"}:
            raise ValueError("probe_mode 必须是 auto 或 strict")
        self._reserved: set[int] = set()
        self._lock = Lock()

    @property
    def port_range(self) -> tuple[int, int]:
        return (self._start, self._end)

    def allocate(self, count: int = 1, *, excluded_ports: Iterable[int] = ()) -> list[int]:
        if count < 1:
            raise ValueError("count 必须为正整数")
        excluded = set(excluded_ports)
        with self._lock:
            available: list[int] = []
            for port in range(self._start, self._end + 1):
                if port in self._reserved or port in excluded or not self._is_free(port):
                    continue
                available.append(port)
                if len(available) >= count:
                    break
            if len(available) < count:
                raise PortAllocationError(
                    f"端口池 {self._start}-{self._end} 空闲端口不足，需要 {count} 个"
                )
            self._reserved.update(available)
            return available

    def release(self, ports: Iterable[int]) -> None:
        with self._lock:
            for port in ports:
                self._reserved.discard(port)

    def _is_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((self._bind_host, port))
            except OSError as error:
                # 受限沙盒可能禁止 bind 探测本身。auto 模式下由跨进程租约和
                # Docker 实际启动结果继续确认；strict 模式用于高安全部署。
                if self._probe_mode == "auto" and error.errno in {errno.EPERM, errno.EACCES}:
                    return True
                return False
            return True

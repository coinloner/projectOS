"""在明确批准后解析依赖并固化项目私有 wheel cache。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse

from app.runtime.manifest import RuntimeCatalog, RuntimeManifest
from app.sandbox.docker_provider import (
    DockerExecutor,
    SubprocessDockerExecutor,
    ensure_image_available,
)


class DependencyFailureKind(str, Enum):
    """控制面可采取不同恢复动作的依赖失败类型。"""

    STORAGE = "storage"
    NETWORK = "network"
    INCOMPATIBLE = "incompatible"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DependencyResolutionResult:
    ok: bool
    cache_path: Path | None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None
    failure_kind: str | None = None
    attempts: int = 0
    recovery_actions: tuple[str, ...] = ()


class DockerDependencyResolver:
    """显式批准后，在联网容器中解析依赖并固化 wheel cache。

    ``requirements.in`` 是依赖意图而非预先生成的 hash lockfile；审批动作本身
    是允许解析的边界。测试阶段只读取已准备的 cache，不再联网。
    """

    def __init__(self, executor: DockerExecutor | None = None) -> None:
        self._executor = executor or SubprocessDockerExecutor()

    def resolve(
        self,
        project_path: str,
        *,
        approved: bool,
        timeout_seconds: int | None = None,
    ) -> DependencyResolutionResult:
        if not approved:
            raise PermissionError("依赖解析需要项目所有者显式批准")
        manifest = RuntimeManifest.load(project_path)
        if not manifest.dependencies_file:
            return DependencyResolutionResult(
                ok=True,
                cache_path=None,
                message="当前 profile 没有第三方依赖",
            )
        root = Path(project_path).resolve()
        profile = RuntimeCatalog.get(manifest.profile)
        dependencies = root / manifest.dependencies_file
        if not dependencies.is_file():
            return DependencyResolutionResult(
                ok=False,
                cache_path=None,
                message=f"依赖声明不存在: {dependencies}",
            )
        digest = hashlib.sha256(dependencies.read_bytes()).hexdigest()
        cache = root / ".sandbox" / "wheels" / digest
        cache.mkdir(parents=True, exist_ok=True)
        image_check = ensure_image_available(self._executor, profile.image)
        if image_check.exit_code != 0:
            return DependencyResolutionResult(
                ok=False,
                cache_path=None,
                stderr=image_check.stderr,
                message="Docker daemon 不可用或受信任基础镜像尚未预置",
            )
        recovery_actions: list[str] = []
        attempts = max(1, int(os.environ.get("PROJECTOS_DEPENDENCY_RETRIES", "3")))
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory)
            isolated_dependencies = input_dir / "requirements.in"
            isolated_dependencies.write_bytes(dependencies.read_bytes())
            # Every attempt is a new --rm container.  A failed pip invocation
            # never becomes the project's usable cache and is removed before
            # the next attempt, so a broken container cannot poison recovery.
            resolve_timeout = timeout_seconds or int(
                os.environ.get("PROJECTOS_DEPENDENCY_RESOLVE_TIMEOUT_SECONDS", "600")
            )
            result = None
            failure_kind: DependencyFailureKind | None = None
            for attempt in range(attempts):
                if attempt:
                    self._clear_partial_cache(cache)
                # 常见生产栈优先下载 wheel，避免 pip 回溯到旧源码包并在容器
                # /tmp 中构建。若发行版确实没有 wheel，最后一次才允许源码包，
                # 且使用项目私有的宿主临时目录绕过 Docker tmpfs 容量限制。
                binary_only = attempt < attempts - 1
                use_host_tmp = failure_kind in {
                    DependencyFailureKind.STORAGE,
                    DependencyFailureKind.INCOMPATIBLE,
                } or attempt == attempts - 1
                if use_host_tmp:
                    recovery_actions.append("host_backed_temp")
                with self._attempt_temp(root, digest, use_host_tmp) as host_tmp:
                    command = self._command(
                        profile.image,
                        input_dir=input_dir,
                        cache=cache,
                        binary_only=binary_only,
                        host_tmp=host_tmp,
                        network_retry=attempt > 0,
                    )
                    result = self._executor.run(command, timeout_seconds=resolve_timeout)
                if result.exit_code == 0:
                    break
                failure_kind = self.classify_failure(result)
                recovery_actions.append(self._recovery_action(failure_kind, attempt))
        assert result is not None
        if result.exit_code == 0:
            (cache / ".projectos-ready").write_text(digest, encoding="utf-8")
        else:
            # 最终失败的半成品不能成为后续运行的隐式输入。
            self._clear_partial_cache(cache)
        final_kind = None if result.exit_code == 0 else self.classify_failure(result)
        return DependencyResolutionResult(
            ok=result.exit_code == 0,
            cache_path=cache if result.exit_code == 0 else None,
            stdout=result.stdout,
            stderr=self._bounded_output(result.stderr),
            message=None if result.exit_code == 0 else self._failure_message(result),
            failure_kind=final_kind.value if final_kind is not None else None,
            attempts=(attempt + 1),
            recovery_actions=tuple(dict.fromkeys(recovery_actions)),
        )

    @staticmethod
    def _command(
        image: str,
        *,
        input_dir: Path,
        cache: Path,
        binary_only: bool,
        host_tmp: Path | None = None,
        network_retry: bool = False,
    ) -> list[str]:
        tmpfs_size = os.environ.get("PROJECTOS_DEPENDENCY_TMPFS_SIZE", "1g")
        command = [
            "docker", "run", "--rm", "--network", "bridge", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        ]
        if host_tmp is None:
            command.extend(["--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}"])
        else:
            command.extend(["--mount", f"type=bind,src={host_tmp},dst=/tmp"])
        command.extend([
            "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
            "--mount", f"type=bind,src={cache},dst=/wheels",
            image, "python", "-m", "pip", "download", "--no-cache-dir",
            "--disable-pip-version-check", "--retries", "8" if network_retry else "5",
            "--timeout", "60" if network_retry else "30", "--prefer-binary",
        ])
        index_url = DockerDependencyResolver._trusted_index_url()
        if index_url:
            command.extend(["--index-url", index_url])
        if binary_only:
            command.extend(["--only-binary", ":all:"])
        command.extend(["-r", "/input/requirements.in", "-d", "/wheels"])
        return command

    @staticmethod
    def _attempt_temp(root: Path, digest: str, enabled: bool):
        if not enabled:
            return _NullTemporaryDirectory()
        recovery_root = root / ".sandbox" / "recovery" / digest
        recovery_root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="pip-", dir=recovery_root)

    @staticmethod
    def _trusted_index_url() -> str | None:
        """只接受 HTTPS 依赖源；未配置时继续使用 pip 官方默认源。"""
        value = os.environ.get("PROJECTOS_PYPI_INDEX_URL", "").strip()
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("PROJECTOS_PYPI_INDEX_URL 必须是不含凭据的 HTTPS URL")
        return value

    @staticmethod
    def _clear_partial_cache(cache: Path) -> None:
        for child in cache.iterdir():
            if child.name == ".projectos-ready":
                child.unlink(missing_ok=True)
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                # The subsequent isolated container will report a useful
                # permission/storage error; do not hide the original failure.
                pass

    @staticmethod
    def _failure_message(result: object) -> str:
        kind = DockerDependencyResolver.classify_failure(result)
        if kind is DependencyFailureKind.STORAGE:
            return "依赖解析失败：Docker 临时存储空间不足；系统已清理半成品并切换宿主临时目录重试，Docker 存储仍不可用"
        if kind is DependencyFailureKind.NETWORK:
            return "依赖解析失败：容器访问依赖源的网络连接不稳定；系统已使用全新容器、扩展连接重试和超时，依赖源仍不可达"
        if kind is DependencyFailureKind.INCOMPATIBLE:
            return "依赖解析失败：依赖声明没有可用的兼容发行版，或版本范围无法收敛"
        if kind is DependencyFailureKind.TIMEOUT:
            return "依赖解析失败：所有受控重试均超过依赖解析时限"
        return "依赖解析失败：系统已完成受控重试，仍未生成可用依赖缓存"

    @staticmethod
    def classify_failure(result: object) -> DependencyFailureKind:
        stderr = str(getattr(result, "stderr", "") or "").lower()
        if "no space left" in stderr or "disk quota exceeded" in stderr:
            return DependencyFailureKind.STORAGE
        if any(token in stderr for token in (
            "ssleof", "remote disconnected", "connection aborted",
            "temporary failure", "name or service not known", "connection reset",
        )):
            return DependencyFailureKind.NETWORK
        if any(token in stderr for token in (
            "no matching distribution", "could not find a version",
            "resolutionimpossible", "conflicting dependencies", "invalid metadata",
        )):
            return DependencyFailureKind.INCOMPATIBLE
        if bool(getattr(result, "timed_out", False)) or getattr(result, "exit_code", None) == 124:
            return DependencyFailureKind.TIMEOUT
        return DependencyFailureKind.UNKNOWN

    @staticmethod
    def _recovery_action(kind: DependencyFailureKind, attempt: int) -> str:
        actions = {
            DependencyFailureKind.STORAGE: "clear_partial_cache_and_use_host_temp",
            DependencyFailureKind.NETWORK: "fresh_container_with_extended_network_retry",
            DependencyFailureKind.INCOMPATIBLE: "binary_first_then_source_fallback",
            DependencyFailureKind.TIMEOUT: "fresh_container_after_timeout",
            DependencyFailureKind.UNKNOWN: "fresh_container_retry",
        }
        return f"attempt_{attempt + 1}:{actions[kind]}"

    @staticmethod
    def _bounded_output(value: str, limit: int = 12_000) -> str:
        if len(value) <= limit:
            return value
        return value[:6_000] + "\n... stderr truncated by ProjectOS ...\n" + value[-6_000:]


class _NullTemporaryDirectory:
    """TemporaryDirectory-compatible context returning no host mount."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None

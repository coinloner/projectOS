"""仅处理依赖锁文件的受控 wheel 获取，不向其暴露项目源码。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

from app.runtime.manifest import RuntimeCatalog, RuntimeManifest
from app.sandbox.docker_provider import DockerExecutor, SubprocessDockerExecutor


@dataclass(frozen=True)
class DependencyResolutionResult:
    ok: bool
    cache_path: Path | None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None


class DockerDependencyResolver:
    """显式批准后，使用 Docker 获取锁定依赖到项目私有 wheel cache。"""

    def __init__(self, executor: DockerExecutor | None = None) -> None:
        self._executor = executor or SubprocessDockerExecutor()

    def resolve(self, project_path: str, *, approved: bool) -> DependencyResolutionResult:
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
        image_check = self._executor.run(
            ["docker", "image", "inspect", profile.image], timeout_seconds=10
        )
        if image_check.exit_code != 0:
            return DependencyResolutionResult(
                ok=False,
                cache_path=None,
                stderr=image_check.stderr,
                message="Docker daemon 不可用或受信任基础镜像尚未预置",
            )
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory)
            isolated_dependencies = input_dir / "requirements.in"
            isolated_dependencies.write_bytes(dependencies.read_bytes())
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "bridge",
                "--read-only",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,src={input_dir},dst=/input,readonly",
                "--mount",
                f"type=bind,src={cache},dst=/wheels",
                profile.image,
                "python",
                "-m",
                "pip",
                "download",
                "--require-hashes",
                "-r",
                "/input/requirements.in",
                "-d",
                "/wheels",
            ]
            result = self._executor.run(command, timeout_seconds=120)
        if result.exit_code == 0:
            (cache / ".projectos-ready").write_text(digest, encoding="utf-8")
        return DependencyResolutionResult(
            ok=result.exit_code == 0,
            cache_path=cache if result.exit_code == 0 else None,
            stdout=result.stdout,
            stderr=result.stderr,
            message=None if result.exit_code == 0 else "依赖解析失败",
        )

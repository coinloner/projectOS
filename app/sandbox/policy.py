"""SandboxPolicy：集中定义默认执行权限。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.runtime.manifest import RuntimeCatalog, RuntimeManifest, RuntimeProfile


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    memory: str = "512m"
    cpus: str = "1"
    pids: int = 128
    tmpfs_size: str = "64m"
    output_char_limit: int = 12_000


@dataclass(frozen=True)
class SandboxSpec:
    project_path: Path
    workspace_path: Path
    check_id: str
    profile: RuntimeProfile
    dependencies_file: Path | None
    wheel_cache: Path | None
    limits: SandboxLimits


class SandboxPolicy:
    """将不可信 Manifest 和 Agent check 请求转换为受控执行规格。"""

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()

    def create_spec(
        self,
        *,
        project_path: str,
        manifest: RuntimeManifest,
        check_id: str,
    ) -> SandboxSpec:
        profile = RuntimeCatalog.get(manifest.profile)
        if check_id not in profile.checks:
            available = ", ".join(sorted(profile.checks))
            raise PermissionError(
                f"profile '{profile.id}' 不允许执行 check '{check_id}'，可用: {available}"
            )
        root = Path(project_path).resolve()
        workspace = root / "workspace"
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace 不存在: {workspace}")

        dependencies_file: Path | None = None
        wheel_cache: Path | None = None
        if manifest.dependencies_file:
            dependencies_file = root / manifest.dependencies_file
            if not dependencies_file.is_file():
                raise FileNotFoundError(f"依赖声明不存在: {dependencies_file}")
            _validate_requirements_in(dependencies_file)
            digest = _sha256(dependencies_file)
            wheel_cache = root / ".sandbox" / "wheels" / digest
            ready_marker = wheel_cache / ".projectos-ready"
            if not wheel_cache.is_dir() or not ready_marker.is_file():
                raise FileNotFoundError(
                    "依赖缓存不存在；请先由受控 DependencyResolver 准备环境"
                )
            if ready_marker.read_text(encoding="utf-8").strip() != digest:
                raise ValueError("依赖缓存与当前 requirements.in 不匹配")

        return SandboxSpec(
            project_path=root,
            workspace_path=workspace,
            check_id=check_id,
            profile=profile,
            dependencies_file=dependencies_file,
            wheel_cache=wheel_cache,
            limits=self._limits,
        )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_requirements_in(path: Path) -> None:
    """限制依赖声明为普通 PyPI 包规格，不接受 URL、路径或 pip 参数。"""
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        forbidden = ("--", "://", "/", "\\", "@", ";", "-e ")
        if any(token in line for token in forbidden):
            raise ValueError(
                f"requirements.in 第 {number} 行包含不允许的依赖来源或 pip 参数"
            )

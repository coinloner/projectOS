"""供 Planner 和领域节点读取的受控 runtime 摘要。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from app.runtime.manifest import RuntimeManifest


@dataclass(frozen=True)
class RuntimeSnapshot:
    manifest_exists: bool
    profile: str | None
    dependencies_configured: bool
    dependency_cache_ready: bool
    error: str | None = None
    startup_scripts_ready: bool = False

    def as_text(self) -> str:
        return "\n".join(
            [
                f"runtime_manifest_exists={self.manifest_exists}",
                f"runtime_profile={self.profile or 'none'}",
                f"dependencies_configured={self.dependencies_configured}",
                f"dependency_cache_ready={self.dependency_cache_ready}",
                f"startup_scripts_ready={self.startup_scripts_ready}",
                *( [f"message={self.error}"] if self.error else [] ),
            ]
        )


def runtime_snapshot(project_path: str) -> RuntimeSnapshot:
    root = Path(project_path)
    try:
        manifest = RuntimeManifest.load(project_path)
    except (FileNotFoundError, ValueError) as error:
        return RuntimeSnapshot(False, None, False, False, str(error), False)
    if not manifest.dependencies_file:
        return RuntimeSnapshot(
            True, manifest.profile, False, True, None,
            _startup_scripts_ready(root),
        )
    dependency_path = root / manifest.dependencies_file
    if not dependency_path.is_file():
        return RuntimeSnapshot(
            True, manifest.profile, True, False, "依赖声明不存在",
            _startup_scripts_ready(root),
        )
    digest = hashlib.sha256(dependency_path.read_bytes()).hexdigest()
    ready = root / ".sandbox" / "wheels" / digest / ".projectos-ready"
    return RuntimeSnapshot(
        True,
        manifest.profile,
        True,
        ready.is_file() and ready.read_text(encoding="utf-8").strip() == digest,
        None,
        _startup_scripts_ready(root),
    )


def _startup_scripts_ready(root: Path) -> bool:
    return all(
        (root / name).is_file()
        for name in ("start.sh", "start.ps1", "start-managed.sh", "docker-compose.yml")
    )

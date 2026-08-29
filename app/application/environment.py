"""受信任的项目运行环境准备器。

EnvironmentAgent 只声明 runtime；本模块由控制平面调用 Docker 适配器完成
白名单镜像准备，并把结果写入项目目录供运维和断点恢复查询。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.runtime.manifest import RuntimeCatalog, RuntimeManifest
from app.runtime.application import ApplicationCatalog
from app.sandbox.docker_provider import (
    DockerExecutor,
    SubprocessDockerExecutor,
    ensure_image_available,
)
from app.sandbox.resolver import DockerDependencyResolver
from app.runtime.startup import ensure_startup_scripts


@dataclass(frozen=True)
class EnvironmentPreparation:
    status: str
    profile: str | None = None
    image: str | None = None
    application: str | None = None
    dependencies: str = "none"
    message: str | None = None
    stderr: str = ""
    failure_kind: str | None = None
    attempts: int = 0
    recovery_actions: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


class EnvironmentProvisioner:
    """准备受信运行时，不接受 Agent 提供的 Docker 命令或镜像。"""

    def __init__(
        self,
        executor: DockerExecutor | None = None,
        *,
        auto_pull_images: bool = True,
    ) -> None:
        self._executor = executor or SubprocessDockerExecutor()
        self._auto_pull_images = auto_pull_images

    def prepare(
        self,
        project_path: str,
        *,
        dependencies_approved: bool = False,
    ) -> EnvironmentPreparation:
        root = Path(project_path).resolve()
        try:
            manifest = RuntimeManifest.load(str(root))
            profile = RuntimeCatalog.get(manifest.profile)
        except (FileNotFoundError, PermissionError, ValueError) as error:
            result = EnvironmentPreparation("invalid_runtime", message=str(error))
            self._persist(root, result)
            return result

        application = ApplicationCatalog.resolve(str(root), manifest.application)
        if application and manifest.application != application:
            manifest = replace(manifest, application=application)
            manifest.save(str(root))
        # 一键启动入口是环境交付的固定产物；脚本只调用控制面 API，
        # 不把 Docker 命令或运行权限交给生成项目。
        ensure_startup_scripts(str(root))

        # 准备 profile 及其全部检查所需的受信镜像（例如 node 检查使用独立镜像）。
        images = {profile.image}
        images.update(
            check.image for check in profile.checks.values() if check.image
        )
        for required_image in sorted(images):
            image = ensure_image_available(self._executor, required_image)
            if image.exit_code != 0 and self._auto_pull_images:
                image = self._executor.run(
                    ["docker", "pull", required_image], timeout_seconds=300
                )
                if image.exit_code == 0:
                    image = ensure_image_available(self._executor, required_image)
            if image.exit_code != 0:
                result = EnvironmentPreparation(
                    "image_unavailable",
                    profile=profile.id,
                    image=required_image,
                    message="Docker daemon 不可用或受信任基础镜像无法准备",
                    stderr=image.stderr,
                )
                self._persist(root, result)
                return result

        resolution_attempts = 0
        resolution_actions: tuple[str, ...] = ()
        if manifest.dependencies_file:
            if not dependencies_approved and not self._has_approved_dependencies(root, manifest):
                result = EnvironmentPreparation(
                    "dependency_approval_required",
                    profile=profile.id,
                    image=profile.image,
                    application=application,
                    dependencies="required",
                    message=(
                        "第三方依赖解析需要项目所有者显式批准："
                        "调用 POST /api/v1/projects/{project_id}/runtime/"
                        "dependency-approvals/approve（状态查询 GET .../dependency-approvals），"
                        "批准后重新运行交付即可继续"
                    ),
                )
                self._persist(root, result)
                return result
            if not _dependency_cache_ready(root, manifest):
                resolution = DockerDependencyResolver(self._executor).resolve(
                    str(root), approved=True
                )
                resolution_attempts = resolution.attempts
                resolution_actions = resolution.recovery_actions
                if not resolution.ok:
                    result = EnvironmentPreparation(
                        "dependency_failed",
                        profile=profile.id,
                        image=profile.image,
                        application=application,
                        dependencies="required",
                        message=resolution.message,
                        stderr=resolution.stderr,
                        failure_kind=resolution.failure_kind,
                        attempts=resolution.attempts,
                        recovery_actions=resolution.recovery_actions,
                    )
                    self._persist(root, result)
                    return result

        result = EnvironmentPreparation(
            "ready",
            profile=profile.id,
            image=profile.image,
            application=application,
            dependencies="required" if manifest.dependencies_file else "none",
            attempts=resolution_attempts,
            recovery_actions=resolution_actions,
        )
        self._persist(root, result)
        return result

    def dependency_approval(self, project_path: str) -> dict[str, Any]:
        """返回当前 requirements.in 对应的审批状态。"""
        root = Path(project_path).resolve()
        try:
            manifest = RuntimeManifest.load(str(root))
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return {"status": "invalid_runtime", "approved": False, "message": str(error)}
        if not manifest.dependencies_file:
            return {"status": "not_required", "approved": True, "dependencies": "none"}
        dependencies = root / manifest.dependencies_file
        if not dependencies.is_file():
            return {"status": "invalid_dependencies", "approved": False, "message": "依赖声明不存在"}
        digest = _dependency_digest(dependencies)
        if self._has_approved_dependencies(root, manifest):
            return {"status": "approved", "approved": True, "digest": digest}
        return {"status": "pending", "approved": False, "digest": digest}

    def approve_dependencies(self, project_path: str) -> dict[str, Any]:
        """记录当前依赖声明的所有者批准，并立即准备依赖缓存。"""
        root = Path(project_path).resolve()
        manifest = RuntimeManifest.load(str(root))
        if not manifest.dependencies_file:
            return self.dependency_approval(str(root))
        dependencies = root / manifest.dependencies_file
        if not dependencies.is_file():
            return self.dependency_approval(str(root))
        directory = root / ".projectos" / "environment"
        directory.mkdir(parents=True, exist_ok=True)
        digest = _dependency_digest(dependencies)
        (directory / "dependency-approval.json").write_text(
            json.dumps(
                {"approved": True, "digest": digest, "approved_at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        result = self.prepare(str(root), dependencies_approved=True)
        return result.as_dict() | {"approval": self.dependency_approval(str(root))}

    def status(self, project_path: str) -> dict[str, Any]:
        path = Path(project_path).resolve() / ".projectos" / "environment" / "preparation.json"
        if not path.is_file():
            return {"status": "not_prepared", "ok": False}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "unknown", "ok": False}

    @staticmethod
    def _persist(root: Path, result: EnvironmentPreparation) -> None:
        directory = root / ".projectos" / "environment"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "preparation.json"
        target.write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _has_approved_dependencies(root: Path, manifest: RuntimeManifest) -> bool:
        if not manifest.dependencies_file:
            return True
        dependencies = root / manifest.dependencies_file
        approval = root / ".projectos" / "environment" / "dependency-approval.json"
        if not dependencies.is_file() or not approval.is_file():
            return False
        try:
            payload = json.loads(approval.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(payload.get("approved")) and payload.get("digest") == _dependency_digest(dependencies)


def _dependency_digest(path: Path) -> str:
    # The digest is also used as the on-disk wheel cache key; keep it byte
    # exact so an existing cache can be reused across process restarts.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dependency_cache_ready(root: Path, manifest: RuntimeManifest) -> bool:
    if not manifest.dependencies_file:
        return True
    dependencies = root / manifest.dependencies_file
    if not dependencies.is_file():
        return False
    digest = _dependency_digest(dependencies)
    marker = root / ".sandbox" / "wheels" / digest / ".projectos-ready"
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest

"""Sandbox 的唯一控制入口。"""

from __future__ import annotations

from pathlib import Path

from app.runtime.manifest import RuntimeManifest
from app.runtime.application import ApplicationCatalog
from app.sandbox.docker_provider import (
    DockerSandboxProvider,
    SubprocessDockerExecutor,
    ensure_image_available,
)
from app.sandbox.policy import SandboxPolicy
from app.sandbox.result import SandboxResult, SandboxStatus


class SandboxController:
    """加载不可信声明、执行策略校验，再委托 Provider 运行固定检查。"""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        provider: DockerSandboxProvider | None = None,
        provisioner=None,
    ) -> None:
        self._policy = policy or SandboxPolicy()
        self._provider = provider or DockerSandboxProvider()
        if provisioner is None:
            from app.application.environment import EnvironmentProvisioner

            provisioner = EnvironmentProvisioner()
        self._provisioner = provisioner

    def run_check(self, project_path: str, check_id: str) -> SandboxResult:
        if check_id == "web-unit" and not _has_web_tests(project_path):
            return SandboxResult(
                status=SandboxStatus.FAILED,
                check_id=check_id,
                runtime_profile=None,
                exit_code=1,
                duration_ms=0,
                message=(
                    "未发现前端 Node 测试文件；web-unit 不允许以 0 个测试用例判定通过"
                ),
            )
        preparation = self._provisioner.prepare(project_path)
        if not preparation.ok:
            return SandboxResult(
                status=SandboxStatus.SETUP_FAILED,
                check_id=check_id,
                runtime_profile=preparation.profile,
                exit_code=None,
                duration_ms=0,
                message=preparation.message,
                stderr=preparation.stderr,
            )
        try:
            manifest = RuntimeManifest.load(project_path)
            application = ApplicationCatalog.resolve(project_path, manifest.application)
            spec = self._policy.create_spec(
                project_path=project_path,
                manifest=manifest,
                check_id=check_id,
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return SandboxResult(
                status=SandboxStatus.SETUP_FAILED,
                check_id=check_id,
                runtime_profile=None,
                exit_code=None,
                duration_ms=0,
                message=str(error),
            )
        return self._provider.run_check(spec)

    def preflight(self, project_path: str, check_id: str = "unit") -> dict[str, object]:
        """在运行前检查 runtime 声明、受信镜像和依赖缓存。"""
        try:
            manifest = RuntimeManifest.load(project_path)
            application = ApplicationCatalog.resolve(project_path, manifest.application)
            spec = self._policy.create_spec(
                project_path=project_path,
                manifest=manifest,
                check_id=check_id,
            )
        except (FileNotFoundError, PermissionError, ValueError) as error:
            return {"ok": False, "status": "invalid_runtime", "message": str(error)}
        executor = getattr(self._provider, "_executor", None) or SubprocessDockerExecutor()
        result = ensure_image_available(executor, spec.image)
        if result.exit_code != 0:
            return {
                "ok": False,
                "status": "image_missing",
                "image": spec.image,
                "message": "受信 Docker 镜像尚未准备；执行测试时控制平面会自动尝试拉取",
                "stderr": result.stderr,
            }
        return {
            "ok": True,
            "status": "ready",
            "image": spec.image,
            "profile": spec.profile.id,
            "check_id": check_id,
            "application": application,
        }


def _has_web_tests(project_path: str) -> bool:
    """匹配 Node --test 的默认发现约定，避免空测试集伪通过。"""
    workspace = Path(project_path).resolve() / "workspace"
    if not workspace.is_dir():
        return False
    for path in workspace.rglob("*.js"):
        if (
            path.name.startswith("test-")
            or path.name.endswith(".test.js")
            or path.name.endswith(".test.mjs")
            or path.name.endswith("_test.js")
        ):
            return True
    return False

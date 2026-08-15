"""Sandbox 的唯一控制入口。"""

from __future__ import annotations

from app.runtime.manifest import RuntimeManifest
from app.sandbox.docker_provider import DockerSandboxProvider
from app.sandbox.policy import SandboxPolicy
from app.sandbox.result import SandboxResult, SandboxStatus


class SandboxController:
    """加载不可信声明、执行策略校验，再委托 Provider 运行固定检查。"""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        provider: DockerSandboxProvider | None = None,
    ) -> None:
        self._policy = policy or SandboxPolicy()
        self._provider = provider or DockerSandboxProvider()

    def run_check(self, project_path: str, check_id: str) -> SandboxResult:
        try:
            manifest = RuntimeManifest.load(project_path)
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

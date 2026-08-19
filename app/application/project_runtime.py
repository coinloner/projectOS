"""项目应用运行的应用层接口。"""

from __future__ import annotations

from app.sandbox.application_runner import (
    ApplicationRun,
    ApplicationRunError,
    DockerApplicationRunner,
)


class ProjectRuntimeService:
    """对 API 暴露固定的 start/status/stop 能力，不暴露 Docker 参数。"""

    def __init__(self, *, runner: DockerApplicationRunner | None = None) -> None:
        self._runner = runner or DockerApplicationRunner()

    def start(self, project_path: str) -> ApplicationRun:
        return self._runner.start(project_path)

    def status(self, run_id: str) -> ApplicationRun:
        return self._runner.status(run_id)

    def stop(self, run_id: str) -> ApplicationRun:
        return self._runner.stop(run_id)

    def shutdown(self) -> None:
        self._runner.shutdown()


__all__ = ["ApplicationRunError", "ProjectRuntimeService"]

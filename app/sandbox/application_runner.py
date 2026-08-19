"""通过 ProjectOS 受控接口运行已生成的项目应用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from app.runtime.application import (
    ApplicationCatalog,
    ApplicationProfile,
    ApplicationService,
)
from app.runtime.manifest import RuntimeCatalog, RuntimeManifest
from app.sandbox.docker_provider import (
    DockerExecutor,
    SubprocessDockerExecutor,
    ensure_image_available,
)


class ApplicationRunStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class ApplicationServiceRun:
    service_id: str
    container_id: str
    host_url: str


@dataclass
class ApplicationRun:
    run_id: str
    project_path: str
    application_id: str
    services: tuple[ApplicationServiceRun, ...]
    status: ApplicationRunStatus
    error: str | None = None
    _container_names: tuple[str, ...] = field(default=(), repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "project_path": self.project_path,
            "application_id": self.application_id,
            "status": self.status.value,
            "error": self.error,
            "services": [
                {
                    "id": service.service_id,
                    "container_id": service.container_id,
                    "url": service.host_url,
                }
                for service in self.services
            ],
        }


class ApplicationRunError(RuntimeError):
    """应用运行配置或 Docker 启动失败。"""


class DockerApplicationRunner:
    """启动和停止应用容器的唯一低层入口。

    调用方只能传入项目路径；镜像、命令、端口、挂载和安全参数全部来自
    ProjectOS 的 RuntimeCatalog/ApplicationCatalog，不能由 Agent 或 HTTP 请求覆盖。
    """

    def __init__(self, executor: DockerExecutor | None = None) -> None:
        self._executor = executor or SubprocessDockerExecutor()
        self._runs: dict[str, ApplicationRun] = {}
        self._lock = Lock()

    def start(self, project_path: str) -> ApplicationRun:
        root = Path(project_path).resolve()
        manifest = RuntimeManifest.load(str(root))
        if not manifest.application:
            raise ApplicationRunError("项目没有声明可运行的 application")
        runtime = RuntimeCatalog.get(manifest.profile)
        application = _application(manifest.application)
        workspace = root / "workspace"
        if not workspace.is_dir():
            raise ApplicationRunError(f"workspace 不存在: {workspace}")

        image_check = ensure_image_available(self._executor, runtime.image)
        if image_check.exit_code != 0:
            raise ApplicationRunError(
                image_check.stderr.strip()
                or f"受信任基础镜像不可用: {runtime.image}"
            )

        run_id = f"app-{uuid4().hex[:12]}"
        container_names: list[str] = []
        started: list[tuple[ApplicationService, str, str]] = []
        try:
            for service in application.services:
                source = workspace / service.workspace_dir
                if not source.is_dir():
                    raise ApplicationRunError(
                        f"应用服务 '{service.id}' 的 workspace 不存在: {source}"
                    )
                mount_source = workspace / service.mount_dir if service.mount_dir else workspace
                if not mount_source.is_dir():
                    raise ApplicationRunError(
                        f"应用服务 '{service.id}' 的挂载目录不存在: {mount_source}"
                    )
                volume_name = None
                if service.data_volume:
                    volume_name = self._prepare_data_volume(
                        project_name=root.name,
                        application_id=application.id,
                        volume_id=service.data_volume,
                        image=runtime.image,
                    )
                container_name = _container_name(root.name, run_id, service.id)
                command = self._docker_run_command(
                    runtime.image,
                    service,
                    mount_source,
                    container_name,
                    volume_name,
                )
                result = self._executor.run(command, timeout_seconds=20)
                if result.exit_code != 0 or not result.stdout.strip():
                    raise ApplicationRunError(
                        result.stderr.strip()
                        or f"服务 '{service.id}' 启动失败"
                    )
                container_id = result.stdout.strip().splitlines()[-1]
                container_names.append(container_name)
                started.append((service, container_id, container_name))
                self._ensure_running(container_name, service.id)
        except Exception:
            for _, _, name in reversed(started):
                self._executor.run(["docker", "stop", name], timeout_seconds=10)
            raise

        application_run = ApplicationRun(
            run_id=run_id,
            project_path=str(root),
            application_id=application.id,
            services=tuple(
                ApplicationServiceRun(
                    service_id=service.id,
                    container_id=container_id,
                    host_url=f"http://127.0.0.1:{service.host_port}",
                )
                for service, container_id, _ in started
            ),
            status=ApplicationRunStatus.RUNNING,
            _container_names=tuple(container_names),
        )
        with self._lock:
            self._runs[run_id] = application_run
        return application_run

    def status(self, run_id: str) -> ApplicationRun:
        with self._lock:
            application_run = self._runs.get(run_id)
        if application_run is None:
            raise ApplicationRunError(f"应用运行不存在: {run_id}")
        if application_run.status is not ApplicationRunStatus.RUNNING:
            return application_run

        for name in application_run._container_names:
            result = self._executor.run(
                ["docker", "inspect", "--format={{.State.Running}}", name],
                timeout_seconds=10,
            )
            if result.exit_code != 0 or result.stdout.strip().lower() != "true":
                application_run.status = ApplicationRunStatus.STOPPED
                break
        return application_run

    def stop(self, run_id: str) -> ApplicationRun:
        with self._lock:
            application_run = self._runs.get(run_id)
        if application_run is None:
            raise ApplicationRunError(f"应用运行不存在: {run_id}")
        if application_run.status is ApplicationRunStatus.RUNNING:
            failures: list[str] = []
            for name in reversed(application_run._container_names):
                result = self._executor.run(["docker", "stop", name], timeout_seconds=15)
                if result.exit_code != 0:
                    failures.append(name)
            if failures:
                application_run.status = ApplicationRunStatus.FAILED
                application_run.error = "停止容器失败: " + ", ".join(failures)
            else:
                application_run.status = ApplicationRunStatus.STOPPED
        return application_run

    def shutdown(self) -> None:
        with self._lock:
            run_ids = tuple(self._runs)
        for run_id in run_ids:
            try:
                self.stop(run_id)
            except ApplicationRunError:
                pass

    def _ensure_running(self, container_name: str, service_id: str) -> None:
        result = self._executor.run(
            ["docker", "inspect", "--format={{.State.Running}}", container_name],
            timeout_seconds=10,
        )
        if result.exit_code != 0 or result.stdout.strip().lower() != "true":
            raise ApplicationRunError(f"服务 '{service_id}' 启动后未保持运行")

    def _prepare_data_volume(
        self,
        *,
        project_name: str,
        application_id: str,
        volume_id: str,
        image: str,
    ) -> str:
        volume_name = _volume_name(project_name, application_id, volume_id)
        created = self._executor.run(
            ["docker", "volume", "create", volume_name], timeout_seconds=10
        )
        if created.exit_code != 0:
            raise ApplicationRunError(
                created.stderr.strip() or f"数据 volume 创建失败: {volume_name}"
            )
        initialized = self._executor.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={volume_name},dst=/data",
                image,
                "sh",
                "-ec",
                "chown 65532:65532 /data && chmod 700 /data",
            ],
            timeout_seconds=20,
        )
        if initialized.exit_code != 0:
            raise ApplicationRunError(
                initialized.stderr.strip() or f"数据 volume 初始化失败: {volume_name}"
            )
        return volume_name

    @staticmethod
    def _docker_run_command(
        image: str,
        service: ApplicationService,
        source: Path,
        container_name: str,
        volume_name: str | None,
    ) -> list[str]:
        command = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--network",
            "bridge",
            "--publish",
            f"127.0.0.1:{service.host_port}:{service.container_port}",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={source},dst=/workspace,readonly",
            "--workdir",
            service.container_workdir,
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        for key, value in service.environment:
            command.extend(["--env", f"{key}={value}"])
        if volume_name:
            command.extend(
                ["--mount", f"type=volume,src={volume_name},dst=/data"]
            )
        command.append(image)
        command.extend(service.command)
        return command


def _application(application_id: str) -> ApplicationProfile:
    return ApplicationCatalog.get(application_id)


def _container_name(project_name: str, run_id: str, service_id: str) -> str:
    return _safe_name(f"projectos-{project_name}-{run_id}-{service_id}")


def _volume_name(project_name: str, application_id: str, volume_id: str) -> str:
    return _safe_name(f"projectos-{project_name}-{application_id}-{volume_id}")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
    return cleaned[:96] or "projectos-runtime"

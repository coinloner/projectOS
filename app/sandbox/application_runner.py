"""通过 ProjectOS 受控接口运行已生成的项目应用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
import time
from threading import Lock
from uuid import uuid4

from app.runtime.application import (
    ApplicationCatalog,
    ApplicationProfile,
    ApplicationService,
)
from app.runtime.manifest import RuntimeCatalog, RuntimeManifest
from app.runtime.port_allocator import PortAllocationError, PortAllocator
from app.runtime.port_lifecycle import PortLease, PortLifecycleManager
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
    host_url: str | None


@dataclass
class ApplicationRun:
    run_id: str
    project_path: str
    application_id: str
    services: tuple[ApplicationServiceRun, ...]
    status: ApplicationRunStatus
    error: str | None = None
    _container_names: tuple[str, ...] = field(default=(), repr=False)
    _network_name: str | None = field(default=None, repr=False)
    _host_ports: tuple[int, ...] = field(default=(), repr=False)
    _port_lease_id: str | None = field(default=None, repr=False)
    _port_lifecycle: PortLifecycleManager | None = field(default=None, repr=False)

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

    def __init__(
        self,
        executor: DockerExecutor | None = None,
        port_allocator: PortAllocator | None = None,
        port_lifecycle: PortLifecycleManager | None = None,
    ) -> None:
        self._executor = executor or SubprocessDockerExecutor()
        self._port_allocator = port_allocator or PortAllocator()
        self._port_lifecycle = port_lifecycle
        self._project_lifecycles: dict[str, PortLifecycleManager] = {}
        self._runs: dict[str, ApplicationRun] = {}
        self._lock = Lock()

    def start(self, project_path: str) -> ApplicationRun:
        root = Path(project_path).resolve()
        manifest = RuntimeManifest.load(str(root))
        application_id = ApplicationCatalog.resolve(str(root), manifest.application)
        if not application_id:
            raise ApplicationRunError("项目没有声明可运行的 application")
        if application_id in {"fastapi-postgres", "fastapi-postgres-web"}:
            entrypoint = root / "workspace" / "backend" / "app" / "main.py"
            if not entrypoint.is_file():
                raise ApplicationRunError(
                    "FastAPI 项目缺少 workspace/backend/app/main.py；"
                    "受信运行时固定启动 app.main:app，请先重新生成或补齐后端入口"
                )
        # 延迟加载避免 application.environment 与 sandbox 包导出形成导入环。
        from app.application.environment import EnvironmentProvisioner

        preparation = EnvironmentProvisioner(self._executor).prepare(str(root))
        if not preparation.ok:
            raise ApplicationRunError(preparation.message or preparation.status)
        manifest = RuntimeManifest.load(str(root))
        runtime = RuntimeCatalog.get(manifest.profile)
        application = _application(application_id)
        workspace = root / "workspace"
        if not workspace.is_dir():
            raise ApplicationRunError(f"workspace 不存在: {workspace}")

        run_id = f"app-{uuid4().hex[:12]}"
        network_name = _network_name(root.name, run_id)
        container_names: list[str] = []
        started: list[tuple[ApplicationService, str, str]] = []
        # 本次启动领用的动态端口；try 块内任何失败路径都必须归还。
        host_ports: tuple[int, ...] = ()
        port_lease: PortLease | None = None
        lifecycle = self._lifecycle_for(root)
        try:
            publishable = [
                service for service in application.services
                if service.publish_port and not service.host_port
            ]
            if publishable:
                try:
                    port_lease = lifecycle.acquire(
                        run_id,
                        len(publishable),
                        allocator=self._port_allocator,
                    )
                    host_ports = port_lease.ports
                except PortAllocationError as error:
                    raise ApplicationRunError(str(error)) from error
            fixed_ports = {
                service.id: service.host_port
                for service in application.services
                if service.host_port
            }
            # service.id -> 本次运行实际使用的 host 端口（动态或固定）。
            resolved_ports = {
                service.id: host_ports[index]
                for index, service in enumerate(publishable)
            } | fixed_ports
            self._ensure_image(runtime.image)
            for service in application.services:
                self._ensure_image(service.image or runtime.image)
            network = self._executor.run(
                ["docker", "network", "create", network_name],
                timeout_seconds=20,
            )
            if network.exit_code != 0:
                raise ApplicationRunError(network.stderr.strip() or "应用网络创建失败")
            for service in application.services:
                source = workspace / service.workspace_dir if service.mount_workspace else workspace
                if service.mount_workspace and not source.is_dir():
                    raise ApplicationRunError(
                        f"应用服务 '{service.id}' 的 workspace 不存在: {source}"
                    )
                mount_source = workspace / service.mount_dir if service.mount_dir else source
                if service.mount_workspace and not mount_source.is_dir():
                    raise ApplicationRunError(
                        f"应用服务 '{service.id}' 的挂载目录不存在: {mount_source}"
                    )
                volume_name = None
                if service.data_volume:
                    volume_name = self._prepare_data_volume(
                        project_name=root.name,
                        application_id=application.id,
                        volume_id=service.data_volume,
                        image=service.image or runtime.image,
                        owner=service.volume_owner,
                    )
                container_name = _container_name(root.name, run_id, service.id)
                command = self._docker_run_command(
                    service.image or runtime.image,
                    service,
                    mount_source,
                    container_name,
                    volume_name,
                    network_name,
                    root,
                    manifest,
                    host_port=resolved_ports.get(service.id, 0),
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
                if service.readiness:
                    self._wait_for_readiness(container_name, service.id, service.readiness)
        except Exception as error:
            if host_ports:
                if port_lease is not None:
                    lifecycle.release(
                        port_lease.lease_id, allocator=self._port_allocator
                    )
                else:
                    self._port_allocator.release(host_ports)
            diagnostics: list[str] = []
            for _, _, name in reversed(started):
                logs = self._executor.run(["docker", "logs", name], timeout_seconds=10)
                if logs.stdout.strip() or logs.stderr.strip():
                    output = "\n".join(
                        part for part in (logs.stdout.strip(), logs.stderr.strip()) if part
                    )
                    diagnostics.append(f"[{name}]\n{output[-4000:]}")
                self._executor.run(["docker", "stop", name], timeout_seconds=10)
                self._executor.run(["docker", "rm", "-f", name], timeout_seconds=10)
            self._executor.run(["docker", "network", "rm", network_name], timeout_seconds=10)
            if diagnostics and isinstance(error, ApplicationRunError):
                raise ApplicationRunError(f"{error}: {' | '.join(diagnostics)}") from error
            raise

        application_run = ApplicationRun(
            run_id=run_id,
            project_path=str(root),
            application_id=application.id,
            services=tuple(
                ApplicationServiceRun(
                    service_id=service.id,
                    container_id=container_id,
                    host_url=(
                        f"http://127.0.0.1:{resolved_ports[service.id]}"
                        if service.publish_port and resolved_ports.get(service.id)
                        else None
                    ),
                )
                for service, container_id, _ in started
            ),
            status=ApplicationRunStatus.RUNNING,
            _container_names=tuple(container_names),
            _network_name=network_name,
            _host_ports=host_ports,
            _port_lease_id=port_lease.lease_id if port_lease is not None else None,
            _port_lifecycle=lifecycle,
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
                self._release_ports(application_run)
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
                self._executor.run(["docker", "rm", "-f", name], timeout_seconds=10)
            if failures:
                application_run.status = ApplicationRunStatus.FAILED
                application_run.error = "停止容器失败: " + ", ".join(failures)
            else:
                application_run.status = ApplicationRunStatus.STOPPED
            if application_run._network_name:
                self._executor.run(
                    ["docker", "network", "rm", application_run._network_name],
                    timeout_seconds=10,
                )
        # 端口释放放在 RUNNING 判断之外：即使容器停止失败，端口也归还
        # （重复 release 幂等，已停止的运行再调 stop 不会误伤）。
        self._release_ports(application_run)
        return application_run

    def _release_ports(self, application_run: ApplicationRun) -> None:
        if application_run._port_lease_id:
            (application_run._port_lifecycle or self._lifecycle_for(Path(application_run.project_path))).release(
                application_run._port_lease_id,
                allocator=self._port_allocator,
            )
            application_run._port_lease_id = None
            application_run._host_ports = ()
        elif application_run._host_ports:
            self._port_allocator.release(application_run._host_ports)
            application_run._host_ports = ()

    def _lifecycle_for(self, root: Path) -> PortLifecycleManager:
        if self._port_lifecycle is not None:
            return self._port_lifecycle
        key = str(root.resolve())
        manager = self._project_lifecycles.get(key)
        if manager is None:
            manager = PortLifecycleManager(
                str(root / ".projectos" / "runtime" / "port-leases.json")
            )
            self._project_lifecycles[key] = manager
        return manager

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

    def _ensure_image(self, image: str) -> None:
        checked = ensure_image_available(self._executor, image)
        if checked.exit_code == 0:
            return
        pulled = self._executor.run(["docker", "pull", image], timeout_seconds=300)
        if pulled.exit_code != 0:
            raise ApplicationRunError(
                pulled.stderr.strip() or f"受信任镜像不可用: {image}"
            )

    def _wait_for_readiness(
        self, container_name: str, service_id: str, readiness: tuple[str, ...]
    ) -> None:
        deadline = time.monotonic() + 45
        last_error = ""
        while time.monotonic() < deadline:
            result = self._executor.run(
                ["docker", "exec", container_name, *readiness], timeout_seconds=10
            )
            if result.exit_code == 0:
                return
            last_error = result.stderr.strip()
            running = self._executor.run(
                ["docker", "inspect", "--format={{.State.Running}}", container_name],
                timeout_seconds=10,
            )
            if running.exit_code != 0 or running.stdout.strip().lower() != "true":
                raise ApplicationRunError(f"服务 '{service_id}' 在就绪前停止")
            time.sleep(0.5)
        raise ApplicationRunError(
            f"服务 '{service_id}' 未在规定时间内就绪" + (f": {last_error}" if last_error else "")
        )

    def _prepare_data_volume(
        self,
        *,
        project_name: str,
        application_id: str,
        volume_id: str,
        image: str,
        owner: str | None,
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
                f"chown {owner or '65532:65532'} /data && chmod 700 /data",
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
        network_name: str,
        project_root: Path,
        manifest: RuntimeManifest,
        host_port: int = 0,
    ) -> list[str]:
        command = [
            "docker",
            "run",
            "--detach",
            "--name",
            container_name,
            "--network",
            network_name,
            "--user",
            service.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=512m",
            "--workdir",
            service.container_workdir,
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        if service.publish_port and host_port:
            command.extend(
                [
                    "--publish",
                    f"127.0.0.1:{host_port}:{service.container_port}",
                ]
            )
        if service.read_only:
            command.insert(command.index("--user"), "--read-only")
        if service.network_alias:
            command.extend(["--network-alias", service.network_alias])
        if service.mount_workspace:
            command.extend(["--mount", f"type=bind,src={source},dst=/workspace,readonly"])
        if manifest.dependencies_file and service.mount_workspace:
            dependencies = project_root / manifest.dependencies_file
            digest = _sha256(dependencies)
            cache = project_root / ".sandbox" / "wheels" / digest
            if not (cache / ".projectos-ready").is_file():
                raise ApplicationRunError("依赖缓存不存在；请先批准并准备环境")
            command.extend(
                [
                    "--tmpfs",
                    "/site-packages:rw,exec,nosuid,nodev,size=1g",
                    "--mount",
                    f"type=bind,src={dependencies},dst=/input/requirements.in,readonly",
                    "--mount",
                    f"type=bind,src={cache},dst=/wheels,readonly",
                    "--env",
                    f"PROJECTOS_DEPENDENCY_DIR=/site-packages/{digest}",
                    "--env",
                    f"PYTHONPATH=/site-packages/{digest}",
                ]
            )
        for key, value in service.environment:
            command.extend(["--env", f"{key}={value}"])
        if volume_name:
            command.extend(
                [
                    "--mount",
                    f"type=volume,src={volume_name},dst={service.volume_mount_dir}",
                ]
            )
        command.append(image)
        command.extend(_runtime_command(service.command, manifest.mode))
        return command


def _runtime_command(command: tuple[str, ...], mode: str) -> tuple[str, ...]:
    """只在显式 development profile 下为受信 Uvicorn 命令打开 reload。"""
    if mode != "development" or not any("uvicorn" in part for part in command):
        return command
    if len(command) == 3 and command[:2] == ("sh", "-ec"):
        script = command[2]
        if "--reload" in script:
            return command
        return (*command[:2], script.replace("--port 8000", "--port 8000 --reload", 1))
    if "--reload" in command:
        return command
    values = list(command)
    try:
        port_index = values.index("--port")
        values[port_index:port_index] = ["--reload"]
    except ValueError:
        values.append("--reload")
    return tuple(values)


def _application(application_id: str) -> ApplicationProfile:
    return ApplicationCatalog.get(application_id)


def _container_name(project_name: str, run_id: str, service_id: str) -> str:
    return _safe_name(f"projectos-{project_name}-{run_id}-{service_id}")


def _volume_name(project_name: str, application_id: str, volume_id: str) -> str:
    return _safe_name(f"projectos-{project_name}-{application_id}-{volume_id}")


def _network_name(project_name: str, run_id: str) -> str:
    return _safe_name(f"projectos-{project_name}-{run_id}-net")


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
    return cleaned[:96] or "projectos-runtime"

"""受信任的项目应用运行目录。

runtime.yaml 只能选择这里已经注册的应用，不允许项目或 Agent 注入 Docker
命令、镜像、挂载或端口。这样应用运行入口始终经过 ProjectOS 的策略层。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApplicationService:
    """一个可由 ProjectOS 启动的固定容器服务。"""

    id: str
    workspace_dir: str
    container_port: int
    # 0 表示由 PortAllocator 从受限端口池动态分配；>0 保留固定端口语义，
    # 仅用于确有固定端口需求的受信服务（当前 catalog 全部为 0）。
    host_port: int
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    data_volume: str | None = None
    mount_dir: str | None = None
    container_workdir: str = "/workspace"
    image: str | None = None
    mount_workspace: bool = True
    publish_port: bool = True
    user: str = "65532:65532"
    network_alias: str | None = None
    readiness: tuple[str, ...] | None = None
    volume_owner: str | None = None
    read_only: bool = True
    volume_mount_dir: str = "/data"


@dataclass(frozen=True)
class ApplicationProfile:
    id: str
    services: tuple[ApplicationService, ...]


class ApplicationCatalog:
    """ProjectOS 允许运行的应用定义。

    这里的命令和 Docker 参数属于控制平面代码。后续增加运行时类型时，
    应通过新增 profile 完成，而不是把任意命令暴露给 HTTP 或 Agent。
    """

    _PROFILES = {
        "todo-web": ApplicationProfile(
            id="todo-web",
            services=(
                ApplicationService(
                    id="backend",
                    workspace_dir="backend",
                    container_port=8000,
                    host_port=0,
                    command=(
                        "python",
                        "-m",
                        "backend",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                    ),
                    environment=(("TODO_DB_PATH", "/data/todo.db"),),
                    data_volume="todo-data",
                    readiness=(
                        "python",
                        "-c",
                        "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=2).close()",
                    ),
                ),
                ApplicationService(
                    id="frontend",
                    workspace_dir="frontend",
                    container_port=8080,
                    host_port=0,
                    command=(
                        "python",
                        "-m",
                        "http.server",
                        "8080",
                        "--bind",
                        "0.0.0.0",
                    ),
                    mount_dir="frontend",
                    readiness=(
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=2).read()",
                    ),
                ),
            ),
        ),
        "fastapi-postgres": ApplicationProfile(
            id="fastapi-postgres",
            services=(
                ApplicationService(
                    id="database",
                    workspace_dir="",
                    container_port=5432,
                    host_port=0,
                    command=("postgres",),
                    environment=(
                        ("POSTGRES_USER", "postgres"),
                        ("POSTGRES_PASSWORD", "postgres"),
                        ("POSTGRES_DB", "taskmgmt"),
                    ),
                    image="postgres:16",
                    mount_workspace=False,
                    publish_port=False,
                    user="999:999",
                    network_alias="db",
                    readiness=("pg_isready", "-U", "postgres", "-d", "taskmgmt"),
                    data_volume="postgres-data",
                    volume_owner="999:999",
                    volume_mount_dir="/var/lib/postgresql/data",
                    container_workdir="/",
                    read_only=False,
                ),
                ApplicationService(
                    id="backend",
                    workspace_dir="backend",
                    container_port=8000,
                    host_port=0,
                    command=(
                        "sh",
                        "-ec",
                        "if [ ! -f \"$PROJECTOS_DEPENDENCY_DIR/.projectos-ready\" ]; then "
                        "mkdir -p \"$PROJECTOS_DEPENDENCY_DIR\" "
                        "&& python -m pip install --no-cache-dir --disable-pip-version-check "
                        "--no-index --find-links=/wheels --target \"$PROJECTOS_DEPENDENCY_DIR\" "
                        "-r /input/requirements.in "
                        "&& touch \"$PROJECTOS_DEPENDENCY_DIR/.projectos-ready\"; fi "
                        "&& if [ -f migrate.py ]; then python migrate.py; "
                        "elif [ -f init_db.py ]; then python init_db.py; fi "
                        "&& if [ -f seed.py ]; then python seed.py; fi "
                        "&& exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
                    ),
                    environment=(
                        ("DATABASE_URL", "postgresql://postgres:postgres@db:5432/taskmgmt"),
                        ("JWT_SECRET", "projectos-local-demo-secret"),
                        ("HOME", "/tmp"),
                        ("PIP_CACHE_DIR", "/tmp/pip-cache"),
                    ),
                    image="python:3.12-slim",
                    data_volume="python-packages",
                    volume_mount_dir="/site-packages",
                    readiness=(
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()",
                    ),
                ),
            ),
        ),
        "fastapi-postgres-web": ApplicationProfile(
            id="fastapi-postgres-web",
            services=(
                ApplicationService(
                    id="database",
                    workspace_dir="",
                    container_port=5432,
                    host_port=0,
                    command=("postgres",),
                    environment=(
                        ("POSTGRES_USER", "postgres"),
                        ("POSTGRES_PASSWORD", "postgres"),
                        ("POSTGRES_DB", "taskmgmt"),
                    ),
                    image="postgres:16",
                    mount_workspace=False,
                    publish_port=False,
                    user="999:999",
                    network_alias="db",
                    readiness=("pg_isready", "-U", "postgres", "-d", "taskmgmt"),
                    data_volume="postgres-data",
                    volume_owner="999:999",
                    volume_mount_dir="/var/lib/postgresql/data",
                    container_workdir="/",
                    read_only=False,
                ),
                ApplicationService(
                    id="backend",
                    # 挂载整个 workspace，FastAPI 入口再从 /workspace/frontend
                    # 提供静态页面；工作目录仍固定在 backend。
                    workspace_dir="",
                    container_port=8000,
                    host_port=0,
                    command=(
                        "sh",
                        "-ec",
                        "if [ ! -f \"$PROJECTOS_DEPENDENCY_DIR/.projectos-ready\" ]; then "
                        "mkdir -p \"$PROJECTOS_DEPENDENCY_DIR\" "
                        "&& python -m pip install --no-cache-dir --disable-pip-version-check "
                        "--no-index --find-links=/wheels --target \"$PROJECTOS_DEPENDENCY_DIR\" "
                        "-r /input/requirements.in "
                        "&& touch \"$PROJECTOS_DEPENDENCY_DIR/.projectos-ready\"; fi "
                        "&& if [ -f migrate.py ]; then python migrate.py; "
                        "elif [ -f init_db.py ]; then python init_db.py; fi "
                        "&& if [ -f seed.py ]; then python seed.py; fi "
                        "&& exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
                    ),
                    environment=(
                        ("DATABASE_URL", "postgresql://postgres:postgres@db:5432/taskmgmt"),
                        ("JWT_SECRET", "projectos-local-demo-secret"),
                        ("HOME", "/tmp"),
                        ("PIP_CACHE_DIR", "/tmp/pip-cache"),
                    ),
                    image="python:3.12-slim",
                    data_volume="python-packages",
                    volume_mount_dir="/site-packages",
                    container_workdir="/workspace/backend",
                    readiness=(
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).read()",
                    ),
                ),
            ),
        ),
        "static-web": ApplicationProfile(
            id="static-web",
            services=(
                ApplicationService(
                    id="web",
                    workspace_dir="",
                    container_port=8081,
                    host_port=0,
                    command=(
                        "python",
                        "-m",
                        "http.server",
                        "8081",
                        "--bind",
                        "0.0.0.0",
                    ),
                    readiness=(
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/', timeout=2).read()",
                    ),
                ),
            ),
        ),
    }

    @classmethod
    def get(cls, application_id: str) -> ApplicationProfile:
        try:
            return cls._PROFILES[application_id]
        except KeyError as error:
            available = ", ".join(sorted(cls._PROFILES))
            raise ValueError(
                f"不支持的 application '{application_id}'，可用: {available}"
            ) from error

    @classmethod
    def detect(cls, project_path: str) -> str | None:
        """从受限的项目形状推断已注册应用，不执行项目代码。"""
        root = Path(project_path)
        workspace = root / "workspace"
        backend = workspace / "backend"
        requirements = root / "requirements.in"
        if (
            (backend / "app" / "main.py").is_file()
            and requirements.is_file()
        ):
            declared = requirements.read_text(encoding="utf-8").lower()
            if "fastapi" in declared and "psycopg" in declared:
                return (
                    "fastapi-postgres-web"
                    if (workspace / "frontend").is_dir()
                    else "fastapi-postgres"
                )
        if (workspace / "backend").is_dir() and (workspace / "frontend").is_dir():
            return "todo-web"
        if (workspace / "index.html").is_file():
            return "static-web"
        return None

    @classmethod
    def resolve(cls, project_path: str, declared: str | None = None) -> str | None:
        """解析项目应用并校验其属于当前进程加载的受信 catalog。"""
        application_id = declared or cls.detect(project_path)
        if application_id is None:
            return None
        cls.get(application_id)
        return application_id

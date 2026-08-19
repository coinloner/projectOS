"""受信任的项目应用运行目录。

runtime.yaml 只能选择这里已经注册的应用，不允许项目或 Agent 注入 Docker
命令、镜像、挂载或端口。这样应用运行入口始终经过 ProjectOS 的策略层。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationService:
    """一个可由 ProjectOS 启动的固定容器服务。"""

    id: str
    workspace_dir: str
    container_port: int
    host_port: int
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    data_volume: str | None = None
    mount_dir: str | None = None
    container_workdir: str = "/workspace"


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
                    host_port=8000,
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
                ),
                ApplicationService(
                    id="frontend",
                    workspace_dir="frontend",
                    container_port=8080,
                    host_port=8080,
                    command=(
                        "python",
                        "-m",
                        "http.server",
                        "8080",
                        "--bind",
                        "0.0.0.0",
                    ),
                    mount_dir="frontend",
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

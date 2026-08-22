# Runtime API 接口文档

生成项目的运行时由 `app.runtime.manifest` 和 `app.runtime.state` 定义；执行本身始终通过 Sandbox API，而不是宿主机 Shell。

## RuntimeManifest

```python
RuntimeManifest(
    version: int,
    profile: str,
    dependencies_file: str | None = None,
    application: str | None = None,
)

RuntimeManifest.load(project_path: str) -> RuntimeManifest
RuntimeManifest.from_dict(payload: object) -> RuntimeManifest
RuntimeManifest.as_dict() -> dict[str, object]
RuntimeManifest.save(project_path: str) -> None
```

`runtime.yaml` 当前只允许：

```yaml
version: 1
profile: python-stdlib # 或 python-pip
dependencies_file: requirements.in # 可选，仅 python-pip
application: todo-web # 可选，仅允许 ApplicationCatalog 中的应用
```

未知字段、未知 profile、非 `requirements.in` 的依赖路径都会抛出 `ValueError`。Manifest 是不可信声明，不能设置镜像、命令、挂载、网络或资源限制。

## RuntimeCatalog

```python
RuntimeCatalog.get(profile_id: str) -> RuntimeProfile
```

当前白名单 profile：

| id | image | fixed check | 第三方依赖 |
|---|---|---|---|
| `python-stdlib` | `python:3.12-slim` | `python -m unittest discover -s tests -v` | 否 |
| `python-pip` | `python:3.12-slim` | 同上 | 是，必须使用 wheel cache |

## RuntimeSnapshot

```python
runtime_snapshot(project_path: str) -> RuntimeSnapshot
RuntimeSnapshot.as_text() -> str
```

快照仅提供控制面摘要：manifest 是否存在、profile、是否声明依赖、依赖 wheel cache 是否就绪，以及可选错误信息。Planner、Code 和 Review 不读取运行时私密配置或 Docker 细节。

测试仍只能通过 ProjectOS 的受控 Sandbox API 发起；应用运行提供两种入口：默认生成的本地
Compose 启动器不依赖控制面，托管启动器则通过 ProjectOS API 发起。

## Project runtime API

应用运行由 ProjectOS API 统一管理，不允许请求体传入命令、镜像、端口、挂载或环境变量：

```text
POST   /api/v1/projects/{project_id}/runtime/runs
GET    /api/v1/projects/{project_id}/runtime/runs/{run_id}
DELETE /api/v1/projects/{project_id}/runtime/runs/{run_id}
GET    /api/v1/projects/{project_id}/runtime/local-status
```

启动时，`RuntimeManifest.application` 选择受信任的 `ApplicationCatalog` 定义。Todo 的
`todo-web` 会启动 backend 和 frontend 两个 Docker 容器；代码目录只读挂载，容器使用
非 root 用户、资源限制和无特权模式，SQLite 数据使用 ProjectOS 管理的 Docker volume。

当 `runtime.yaml` 未显式声明 `application` 时，控制平面会执行受限形状识别。目前支持识别
包含 `workspace/backend/app/main.py` 且声明 FastAPI 与 psycopg 依赖的项目；若存在
`workspace/frontend` 自动选择 `fastapi-postgres-web`，否则选择 `fastapi-postgres`。
它会复用已批准的 wheel cache，创建项目隔离网络，启动 PostgreSQL；可选的 `migrate.py`/
`seed.py` 存在时才执行，再启动 Uvicorn。Web profile 将整个 workspace 挂载到后端容器，
使 FastAPI 可以提供 `workspace/frontend`。数据库不绑定宿主机端口，其余服务由 `PortAllocator`
从受限端口池（默认 `8100-8299`）动态分配宿主端口。

应用服务需要宿主机回环端口才能被浏览器访问，因此运行容器使用 Docker bridge 网络，
分配到的宿主端口只绑定 `127.0.0.1`。启动失败或停止运行后端口自动归还，供下一次启动复用。
这与一次性 unit 测试的 `--network none` 不同；应用运行的网络策略后续还可以替换为带出口
防火墙的专用网络。

独立模式写入 `.projectos/runtime/local-run.json`，状态接口会读取并对记录中的容器做探测；
项目可先执行 `start.sh`，之后通过 `POST /api/v1/projects/import` 登记到控制面，再使用
`local-status` 观察，而不需要停止或重启项目。

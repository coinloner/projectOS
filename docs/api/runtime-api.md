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

当前系统不提供宿主机 Shell API。生成项目的构建、运行与测试只能通过 ProjectOS 的受控 API 发起。

## Project runtime API

应用运行由 ProjectOS API 统一管理，不允许请求体传入命令、镜像、端口、挂载或环境变量：

```text
POST   /api/v1/projects/{project_id}/runtime/runs
GET    /api/v1/projects/{project_id}/runtime/runs/{run_id}
DELETE /api/v1/projects/{project_id}/runtime/runs/{run_id}
```

启动时，`RuntimeManifest.application` 选择受信任的 `ApplicationCatalog` 定义。Todo 的
`todo-web` 会启动 backend 和 frontend 两个 Docker 容器；代码目录只读挂载，容器使用
非 root 用户、资源限制和无特权模式，SQLite 数据使用 ProjectOS 管理的 Docker volume。

应用服务需要宿主机回环端口才能被浏览器访问，因此运行容器使用 Docker bridge 网络并只
绑定 `127.0.0.1`。这与一次性 unit 测试的 `--network none` 不同；应用运行的网络策略
后续还可以替换为带出口防火墙的专用网络。

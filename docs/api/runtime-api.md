# Runtime API 接口文档

生成项目的运行时由 `app.runtime.manifest` 和 `app.runtime.state` 定义；执行本身始终通过 Sandbox API，而不是宿主机 Shell。

## RuntimeManifest

```python
RuntimeManifest(
    version: int,
    profile: str,
    dependencies_file: str | None = None,
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

`app.runtime.Runtime` 仍保留用于未来人工确认的宿主机 Shell 逃生舱；它不是当前生成项目的构建、运行或测试入口。

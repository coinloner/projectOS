# Runtime 模块

`app.runtime` 定义生成项目的运行时声明与受控状态摘要。它不执行生成项目的代码，真正执行由 `app.sandbox` 完成。

## 组成

| 模块 | 职责 |
|---|---|
| `manifest.py` | 校验 `runtime.yaml`，维护受信任的 `RuntimeCatalog` |
| `application.py` | 维护受信任的应用和服务目录，并对生成项目做受限应用识别 |
| `state.py` | 将 manifest 和 wheel cache 转换为 Planner/Agent 可读的 `RuntimeSnapshot` |

## 信任模型

`runtime.yaml` 是由 BootstrapAgent 写入的输入，不是权限配置。它只能声明已白名单的 profile、可选 `requirements.in` 和可选 application；镜像、检查命令、应用启动命令、Docker 挂载、网络和资源限制都固定在 ProjectOS 代码中。

```text
runtime.yaml (untrusted)
  -> RuntimeManifest validation
  -> RuntimeCatalog profile
  -> ApplicationCatalog / SandboxPolicy
  -> Docker ApplicationSpec / SandboxSpec (trusted)
```

## 当前 Profile

- `python-stdlib`：无第三方依赖，可执行固定 unittest check。
- `python-pip`：需要已批准的 Resolver 根据 `requirements.in` 解析并创建项目私有 wheel cache，之后仍在无网测试容器中离线安装。`requirements.in` 不要求 Agent 预先生成 hash lockfile。

两个 Profile 都额外暴露 `web-unit` check：固定使用 `node:22-alpine` 镜像执行 `node --test`，自动发现 Node 默认测试文件；若没有任何前端测试文件，控制器会直接失败，不能把 0 个用例误报为通过。

`RuntimeSnapshot` 只向 Planner、Code 和 Review 暴露“是否存在声明、profile、是否配置依赖、缓存是否就绪”等摘要，避免将 Docker 或宿主机细节泄露给 Agent。

应用运行有两条明确路径：默认 `start.sh`/`start.ps1`/macOS `start.command` 使用由
ApplicationCatalog 生成的受信 `docker-compose.yml` 独立启动，不依赖 ProjectOS HTTP 服务；
`start-managed.sh`/`start-managed.ps1` 才调用 `/runtime/runs`，由控制面统一管理容器和端口。
独立运行状态写入 `.projectos/runtime/local-run.json`，控制面通过 local-status 接口读取并探测，
因此项目可以先独立运行，之后再纳入 ProjectOS 监控。

当前 `fastapi-postgres` 与 `fastapi-postgres-web` profile 会自动复用依赖 wheel cache，创建项目
隔离网络，启动受信任的 PostgreSQL 服务，并按项目声明安装依赖后启动 FastAPI；可选的
`migrate.py`/`seed.py` 存在时才执行。数据库和应用容器都由同一个运行记录管理，停止运行时会
一并清理容器和网络。

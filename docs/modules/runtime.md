# Runtime 模块

`app.runtime` 定义生成项目的运行时声明与受控状态摘要。它不执行生成项目的代码，真正执行由 `app.sandbox` 完成。

## 组成

| 模块 | 职责 |
|---|---|
| `manifest.py` | 校验 `runtime.yaml`，维护受信任的 `RuntimeCatalog` |
| `application.py` | 维护受信任的应用和服务目录，不接受任意启动命令 |
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
- `python-pip`：需要已批准的 Resolver 创建 hash 对应 wheel cache，之后仍在无网测试容器中离线安装。

`RuntimeSnapshot` 只向 Planner、Code 和 Review 暴露“是否存在声明、profile、是否配置依赖、缓存是否就绪”等摘要，避免将 Docker 或宿主机细节泄露给 Agent。

应用运行必须通过 ProjectOS API 的 `/runtime/runs` 接口。API 只接收项目标识，运行层根据
manifest 和 ApplicationCatalog 生成固定 Docker 命令；不存在宿主机 shell 回退。

# Runtime 模块

`app.runtime` 定义生成项目的运行时声明与受控状态摘要。它不执行生成项目的代码，真正执行由 `app.sandbox` 完成。

## 组成

| 模块 | 职责 |
|---|---|
| `manifest.py` | 校验 `runtime.yaml`，维护受信任的 `RuntimeCatalog` |
| `state.py` | 将 manifest 和 wheel cache 转换为 Planner/Agent 可读的 `RuntimeSnapshot` |
| `Runtime.py` | 遗留的宿主机受控命令入口，仅供未来人工确认的逃生舱 |

## 信任模型

`runtime.yaml` 是由 BootstrapAgent 写入的输入，不是权限配置。它只能声明已白名单的 profile 与可选 `requirements.in`；镜像、检查命令、Docker 挂载、网络和资源限制都固定在 ProjectOS 代码中。

```text
runtime.yaml (untrusted)
  -> RuntimeManifest validation
  -> RuntimeCatalog profile
  -> SandboxPolicy
  -> Docker SandboxSpec (trusted)
```

## 当前 Profile

- `python-stdlib`：无第三方依赖，可执行固定 unittest check。
- `python-pip`：需要已批准的 Resolver 创建 hash 对应 wheel cache，之后仍在无网测试容器中离线安装。

`RuntimeSnapshot` 只向 Planner、Code 和 Review 暴露“是否存在声明、profile、是否配置依赖、缓存是否就绪”等摘要，避免将 Docker 或宿主机细节泄露给 Agent。

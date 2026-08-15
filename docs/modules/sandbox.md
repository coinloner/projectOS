# Sandbox 模块

## 概述

Sandbox 用于执行 Agent 生成的项目代码。Agent 只能请求固定 check，不能获得 Docker Socket、宿主机 shell、容器镜像名、挂载路径或网络策略。

## 执行链路

```text
RuntimeManifest -> SandboxPolicy -> SandboxSpec -> DockerSandboxProvider
```

- `runtime.yaml` 是不可信声明，只能选择 ProjectOS `RuntimeCatalog` 中的 Profile。
- `SandboxPolicy` 固定 check、资源限制、只读挂载和依赖缓存位置。
- `DockerSandboxProvider` 使用无网络、非 root、只读根文件系统执行测试。

## 默认限制

- `--network none`
- `--read-only`
- `--user 65532:65532`
- `--cap-drop ALL`
- `--security-opt no-new-privileges`
- CPU、内存、PID、时间和输出大小限制
- workspace 只读挂载，不挂载 Docker Socket、用户 Home 或密钥

## 动态依赖

`python-pip` Profile 只接受受限格式的 `requirements.in`。它不允许 URL、路径、editable 安装或任意 pip 参数。

依赖 Resolver 需要项目所有者批准；它只挂载依赖声明和 wheel cache，允许网络下载受控依赖，不挂载项目 workspace。Resolver 将 wheel cache 固化为当前依赖声明的快照；测试容器从该快照离线安装依赖并执行固定检查。

## 当前结果边界

`SandboxController.run_check()` 返回结构化 `SandboxResult`，其中包含状态、check id、runtime profile、退出码、耗时和受限长度的标准输出/错误输出。`TestAgent` 当前把它转换为文本写入 `tests.md`。

这意味着 Docker 的实际执行已受控，但结果还没有作为独立 `SandboxEvidence` 持久化，也不会自动触发 Planner 的修复决策。下一阶段会把执行证据、重试次数和终态接入 Workflow 控制面。

`python-pip` 的 Resolver 已实现依赖格式校验和 wheel cache 准备；“请求所有者批准、解析依赖并恢复 GraphRunner”的流程尚未实现。Agent 不能自动触发这个网络阶段。

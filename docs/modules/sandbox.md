# Sandbox 模块

## 概述

Sandbox 用于执行和运行 Agent 生成的项目代码。Agent 只能请求固定 check，不能获得 Docker Socket、宿主机 shell、容器镜像名、挂载路径或网络策略。

应用启动由 `DockerApplicationRunner` 统一执行。它只接受项目路径，应用服务的镜像、命令、端口、挂载和权限由 `ApplicationCatalog` 固定定义；HTTP 层和 Agent 都不能覆盖这些字段。

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

`SandboxController.run_check()` 返回结构化 `SandboxResult`，其中包含状态、check id、runtime profile、退出码、耗时和受限长度的标准输出/错误输出。Test 的 `run_sandbox_check` 工具在返回给 Agent 前会将其记录为 `SandboxEvidence`。

证据文件位于 `.projectos/runs/<trace_id>/evidence/<evidence_id>.json`，记录生成它的 WorkItem 和 Agent。工具返回值保留 `evidence_id` 与可读摘要，`tests.md` 是人读报告而非结果真相来源；Review 可读取同一 Trace 的原始证据。

测试失败会触发 `needs_replan`，主入口最多请求两轮 Repair Plan。更完整的跨进程恢复、持久化预算和可变 DAG 状态机仍未实现。

Test WorkItem 的完成还要求至少存在一条由当前 WorkItem 产生的 `SandboxEvidence`。没有证据时，Runner 只进行有限重跑，之后以 `test_evidence_missing` 失败，而不会接受 LLM 的文字声明。

`python-pip` 的 Resolver 已实现依赖格式校验和 wheel cache 准备；“请求所有者批准、解析依赖并恢复 GraphRunner”的流程尚未实现。Agent 不能自动触发这个网络阶段。

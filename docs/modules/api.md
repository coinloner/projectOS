# API 模块

`app.api` 是 ProjectOS 的 HTTP 接入层。它不注册 Agent、Tool 或 Workflow，也不拥有
编排逻辑；每个请求通过 `RunService` 构造项目 Container，再使用已注册的受控 Workflow。

## 启动

```bash
uvicorn app.api.asgi:app --reload
```

默认项目根目录为 `./projects`。API 的 project id 只允许字母、数字、下划线和连字符，
不会接收宿主机路径。

## 当前接口

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 存活检查 |
| `POST` | `/api/v1/projects` | 创建 ProjectOS 项目 |
| `GET` | `/api/v1/projects/{project_id}/workflows` | 列出可由 API 直接启动的受控 Workflow |
| `POST` | `/api/v1/projects/{project_id}/runs` | 后台启动一个受控 Workflow |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}` | 查询 Trace 与进程内运行状态 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/events` | 查询持久化控制面事件 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/memory` | 查询一次运行的会话记忆事件 |

启动运行的请求只接受 `goal` 和已注册的 `workflow_id`：

```json
{
  "goal": "根据现有需求设计系统架构",
  "workflow_id": "architecture_parallel"
}
```

调用方不能提交 Agent id、Tool、执行模式、文件路径、slot 或发布授权。`RunService` 会检查
模板声明的已发布前置产物；当前架构 Workflow 都需要 `requirement.md`。

- `architecture_compact`：小需求的低成本路径，两个 LLM 节点。
- `architecture_parallel`：复杂需求的多分区路径，baseline 后可并行运行多个 scope。
- `project_delivery_minimal`：从已有需求和架构继续跑任务、环境、并行代码、测试和 Review。

## 运行模型

请求同步完成受控模板的计划编译，随后由进程内 `RunCoordinator` 在线程池执行 Agent。
`trace_id` 是持久化追踪标识，`Future` 仅用于当前进程报告 `running` 状态。进程重启后，
Trace 仍可查询，受校验的 `/resume` 已支持从最近 checkpoint 恢复未完成节点；当前仍是进程内线程池，
后续可以替换为外部任务队列而不改变 API 契约。

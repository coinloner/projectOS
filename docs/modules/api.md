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
| `GET` | `/api/v1/llm/providers` | 返回可供用户选择的 provider、默认模型和兼容端点（不返回密钥） |
| `GET` | `/api/v1/llm/providers/{provider}/models` | 从 provider 动态读取可用模型 ID |
| `POST` | `/api/v1/projects` | 创建 ProjectOS 项目 |
| `GET` | `/api/v1/projects/{project_id}/workflows` | 列出可由 API 直接启动的受控 Workflow |
| `GET` | `/api/v1/projects/{project_id}/runtime/preflight` | 检查 runtime 声明、Docker 镜像和依赖前置 |
| `POST` | `/api/v1/projects/{project_id}/runs` | 后台启动一个受控 Workflow |
| `POST` | `/api/v1/projects/{project_id}/conversations` | 创建连续对话会话 |
| `POST` | `/api/v1/projects/{project_id}/conversations/{conversation_id}/messages` | 发送消息并启动一轮动态 Planner |
| `GET` | `/api/v1/projects/{project_id}/conversations/{conversation_id}` | 查询会话和消息 |
| `GET` | `/api/v1/projects/{project_id}/conversations/{conversation_id}/messages` | 查询会话消息 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}` | 查询 Trace 与进程内运行状态 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/events` | 查询持久化控制面事件 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/capabilities` | 查询待审批的动态能力候选 |
| `POST` | `/api/v1/projects/{project_id}/runs/{trace_id}/capabilities/approve` | 批准 source 并立即恢复运行 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/baseline` | 查询计划基线和当前修订 |
| `GET` | `/api/v1/projects/{project_id}/runs/{trace_id}/memory` | 查询一次运行的会话记忆事件 |

启动运行的请求可以携带用户选择的 `provider`、`model` 和 `base_url`；不传时使用部署默认值：

```json
{
  "goal": "根据现有需求设计系统架构",
  "workflow_id": "architecture_parallel",
  "provider": "siliconflow",
  "model": "zai-org/GLM-5.2",
  "llm_overrides": {
    "review_agent": {
      "provider": "openai",
      "model": "gpt-4.1"
    }
  }
}
```

调用方不能提交 Agent id、Tool、执行模式、文件路径、slot 或发布授权。`RunService` 会检查
模板声明的已发布前置产物；当前架构 Workflow 都需要 `requirement.md`。

- `architecture_compact`：小需求的低成本路径，两个 LLM 节点。
- `architecture_parallel`：复杂需求的多分区路径，baseline 后可并行运行多个 scope。
- `architecture_layered`：三层结构化架构路径，按总体蓝图、模块设计和实现准备对象分阶段并行。
- `project_delivery_minimal`：从已有需求和架构继续跑任务、环境、并行代码、测试和 Review。

连续对话接口不要求调用方先选择 Workflow。系统运行路径只有两类：调用方明确选择的受控 Workflow，
或由 Planner 动态生成 DAG。会话层先用确定性规则分类意图：新需求/修改需求进入
普通 `PlannerService.plan()`，查看结果只读取 Trace、Artifact 和 SandboxEvidence，继续只返回
当前任务状态，恢复才调用 checkpoint 恢复入口；授权消息不会隐式授予能力。会话的**首条** user 消息
就是完整目标，原样交给 Planner 做全量规划；后续 user 消息才携带有限历史进入规划，并要求 Planner
结合上下文只生成本轮的最小可执行计划。Planner 生成的本轮计划仍然拥有独立 `trace_id`。会话保存连续性，
Trace 保存一次执行的审计事实，两者不混在同一个文件中。每次动态计划都会保存计划基线；会话中的
修改请求生成局部 `PlanPatch`，只重跑受影响子图，不自动重建整条执行链。Trace 进入终态后，读取会话接口会追加
基于事实生成的 assistant 自然语言摘要，前端无需直接解析控制面事件。

发送会话消息时也可以携带同样的 `provider`、`model`、`base_url` 字段。用户选择会被解析为本轮
不可变的 `LLMSelection` 并写入 Trace；Planner、所有 Agent、隔离 Worker、修复和断点恢复都会
沿用该选择，不依赖修改 `.env` 或进程级全局变量。

`llm_overrides` 是 Pro 能力：键为已注册 Agent ID（也可以使用 `planner` 单独配置规划器），值为
该 Agent 的 provider/model 配置。未列出的 Agent 自动回退到本轮默认模型。建议至少让
`code_agent` 与 `review_agent` 使用不同的模型或不同 provider，避免“自己实现、自己审批”的单模型闭环；
系统只提供路由能力，不强制绑定某一家模型。

## 运行模型

请求同步完成受控模板的计划编译，随后由进程内 `RunCoordinator` 在线程池执行 Agent。
`trace_id` 是持久化追踪标识，`Future` 仅用于当前进程报告 `running` 状态。进程重启后，
Trace 仍可查询，受校验的 `/resume` 已支持从最近 checkpoint 恢复未完成节点；当前仍是进程内线程池，
后续可以替换为外部任务队列而不改变 API 契约。

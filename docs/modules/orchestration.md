# Orchestration 模块

`app.orchestration` 是 ProjectOS 的运行时编排层：它接收已经验证的计划，按 WorkItem 依赖调度 Agent，并记录本次执行的状态与 Trace。

```text
ExecutionPlan
  -> WorkItem DAG
  -> GraphRunner
  -> RunState + TraceStore
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `WorkItem` | 一次 Trace 中可执行的最小任务：Agent、目标、产物、依赖与完成标准 |
| `WorkItemDependency` | 一条依赖及来源：system/template/planner |
| `ExecutionPlan` | 本次运行的合法 WorkItem DAG |
| `GraphRunner` | 找到 ready WorkItem，创建 Agent，执行并处理失败/能力缺口 |
| `RunState` | 当前进程内的结果和 artifact 文本 |
| `ExecutionContext` | GraphRunner 为单个 WorkItem 创建的可信 `trace_id/work_item_id/agent_id` 身份 |
| `TraceStore` | 持久化计划、事件、终态、requirement 修订和 sandbox evidence |
| `SandboxEvidence` | Docker check 的状态、退出码、耗时和原始受限输出，绑定到 Trace 与 WorkItem |

## 边界

- Orchestration 不理解 requirement、代码、测试或 Docker 的领域内容。
- Orchestration 不决定项目应该有哪些工作项，这是 Planner 的职责。
- Orchestration 不保存可复用流程经验，这是 Workflow 的职责。
- Agent 和 Domain Service 不直接修改 WorkItem 状态或 Trace；只有 GraphRunner 与 TraceStore 可以写入。
- `ExecutionContext` 经 ToolGateway 绑定到工具对象，不会写入 Agent task 文本或 ToolDef schema。

当前 GraphRunner 是同步串行执行器。WorkItem、事件和 Docker 证据可追溯，但 RunState 尚不支持跨进程恢复，且没有失败后的 PlanPatch 或重试状态机。

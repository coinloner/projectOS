# Workflow 模块

`app.workflow` 是通用任务图执行层。它不理解需求、代码或 Docker 细节，只执行受校验的 `ExecutionPlan`，并记录本次运行的节点状态。

## 核心对象

| 对象 | 职责 |
|---|---|
| `WorkflowTemplate` | 常见流程经验，由 `TaskBlueprint` 组成，供 Planner 参考 |
| `WorkflowTemplateRegistry` | 已注册模板的目录 |
| `ExecutionPlan` | 一次 Trace 中可执行的 WorkItem DAG |
| `WorkItem` | 一个可追踪任务：Agent、目标、产物 key、依赖和可选完成标准 |
| `WorkItemDependency` | 一条依赖及其来源：`system`、`template` 或 `planner` |
| `TraceStore` | 持久化计划、WorkItem 事件和 requirement 修订快照 |
| `GraphRunner` | 按依赖调度同步 WorkItem，处理失败和能力缺口 |
| `RunState` | 进程内的 `work_item_id -> NodeResult` 与 `output_key -> text` |
| `NodeResult` | Graph 层统一的成功、失败或能力缺口结果 |
| `GraphRunResult` | 一次执行的终态、状态和可选候选 source |

## 默认模板

`project_delivery_template()` 定义当前项目交付的默认经验：

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
```

Template 不是实际运行状态，也不会直接创建 Agent。Planner 可以把它作为 hint；其默认依赖会在 `PlanValidator` 中应用到本次选中的 WorkItem。Planner 可以提供有原因的 override 移除模板依赖，但不能移除系统依赖。

依赖来源的优先级为：

```text
system    可运行性与交付安全前置条件，不可移除
template  常见项目交付顺序，可提供原因后移除
planner   本次项目额外声明的依赖
```

## 当前运行语义

```text
PlannerService.plan()
  -> TraceStore.start_trace()
  -> ExecutionPlan
  -> GraphRunner.run(plan)
  -> RunState.ready_items()
  -> AgentRegistry.create(agent_id).run(task)
  -> AgentResult -> NodeResult
  -> RunState.record()
  -> TraceStore.events.jsonl
```

GraphRunner 是同步串行执行器；即使多个节点已经 ready，也会按计划顺序依次执行。后继 Agent 只得到可用 artifact key，正文必须经自己的受限工具读取。

| `GraphRunStatus` | 含义 |
|---|---|
| `completed` | 所有计划节点均成功 |
| `waiting_for_capability_approval` | 节点提出能力缺口，存在候选动态 source，等待上层授权 |
| `blocked` | 节点提出能力缺口，但没有候选 source |
| `failed` | Agent 未注册、节点异常或节点失败 |

每次 Planner 运行会在 `projects/<project>/.projectos/` 建立稳定的 `requirement_id` 与新的 `trace_id`。GraphRunner 会保存 `plan.json`、WorkItem 计划/开始/结束事件；Requirement 成功时会按内容 hash 保存修订快照。

当前 Runner 不会自动激活 MCP，也没有已持久化 RunState 的恢复执行能力。

## 当前边界与下一步

- Runner 不 import 领域模块，不负责质量评分、依赖安装或 Docker 细节。
- `RunState` 仅存在于一次进程内，不能作为跨运行工作流状态。
- WorkItem 的计划和事件已持久化，但进程内 `RunState` 尚不能恢复。
- 失败会终止当前执行；目前没有 `PlanPatch`、局部重规划或自动重试。
- 下一版应以 `SandboxEvidence`、WorkItem 状态机和明确的状态转移扩展控制面。

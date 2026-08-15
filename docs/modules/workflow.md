# Workflow 模块

`app.workflow` 是通用任务图执行层。它不理解需求、代码或 Docker 细节，只执行受校验的 `ExecutionPlan`，并记录本次运行的节点状态。

## 核心对象

| 对象 | 职责 |
|---|---|
| `WorkflowTemplate` | 常见流程经验，由 `TaskBlueprint` 组成，供 Planner 参考 |
| `WorkflowTemplateRegistry` | 已注册模板的目录 |
| `ExecutionPlan` | 一次运行的纯数据 DAG |
| `TaskNode` | 一个 Agent 节点：Agent、目标、依赖、输出 key |
| `GraphRunner` | 按依赖调度同步节点，处理失败和能力缺口 |
| `RunState` | 运行内的 `node_id -> NodeResult` 与 `output_key -> text` |
| `NodeResult` | Graph 层统一的成功、失败或能力缺口结果 |
| `GraphRunResult` | 一次执行的终态、状态和可选候选 source |

## 默认模板

`project_delivery_template()` 定义当前项目交付的默认经验：

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
```

Template 不是实际运行状态，也不会直接创建 Agent。Planner 可以把它作为 hint，最后由 `PlanValidator` 将不可信 `PlanDraft` 转成 `ExecutionPlan`。

## 当前运行语义

```text
PlannerService.plan()
  -> ExecutionPlan
  -> GraphRunner.run(plan)
  -> ready_nodes()
  -> AgentRegistry.create(agent_id).run(task)
  -> AgentResult -> NodeResult
  -> RunState.record()
```

GraphRunner 是同步串行执行器；即使多个节点已经 ready，也会按计划顺序依次执行。后继 Agent 只得到可用 artifact key，正文必须经自己的受限工具读取。

| `GraphRunStatus` | 含义 |
|---|---|
| `completed` | 所有计划节点均成功 |
| `waiting_for_capability_approval` | 节点提出能力缺口，存在候选动态 source，等待上层授权 |
| `blocked` | 节点提出能力缺口，但没有候选 source |
| `failed` | Agent 未注册、节点异常或节点失败 |

当前 Runner 不会自动激活 MCP，也没有已持久化 RunState 的恢复执行能力。

## 当前边界与下一步

- Runner 不 import 领域模块，不负责质量评分、依赖安装或 Docker 细节。
- `RunState` 仅存在于一次进程内，不能作为跨运行工作流状态。
- 节点失败会终止当前执行；目前没有 `PlanPatch`、局部重规划或自动重试。
- 下一版应以 `WorkItem`、`SandboxEvidence` 和明确的状态转移扩展控制面，而不是把领域判断继续写入 Runner。

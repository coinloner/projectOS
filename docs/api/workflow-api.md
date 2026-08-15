# Workflow API 接口文档

## 计划数据

```python
TaskNode(
    id: str,
    agent_id: str,
    objective: str,
    output_key: str,
    policy_id: str | None = None,
    depends_on: tuple[str, ...] = (),
)

ExecutionPlan(
    id: str,
    goal: str,
    nodes: tuple[TaskNode, ...],
    template_id: str | None = None,
)
```

`ExecutionPlan` 必须至少包含一个节点，并拒绝重复 node id、未知依赖和循环依赖。`node(node_id)` 与 `root_nodes()` 用于只读查询。

## 模板

```python
TaskBlueprint(...).instantiate() -> TaskNode
WorkflowTemplate(...).instantiate(plan_id: str, goal: str) -> ExecutionPlan
WorkflowTemplateRegistry.register(template) -> None
WorkflowTemplateRegistry.get(template_id) -> WorkflowTemplate | None
WorkflowTemplateRegistry.templates() -> tuple[WorkflowTemplate, ...]
```

Template 是可复用的流程经验，不保存运行状态，也不携带 Agent 实例或工具。

## 执行

```python
GraphRunner(agents: AgentRegistry, tools: ToolGateway)
GraphRunner.run(plan: ExecutionPlan) -> GraphRunResult
```

`GraphRunner` 按依赖顺序同步执行节点，并把 `AgentResult` 转为 `NodeResult` 写入 `RunState`。

| `GraphRunStatus` | 说明 |
|---|---|
| `completed` | 全部节点完成 |
| `waiting_for_capability_approval` | 存在动态 source 候选，等待上层批准 |
| `blocked` | 没有可满足的动态 source |
| `failed` | 未注册 Agent、节点异常或失败结果 |

当前 `GraphRunner` 没有持久化、恢复、并发、局部重规划或重试。测试失败后的修复循环属于下一版控制面能力。

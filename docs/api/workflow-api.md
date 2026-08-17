# Workflow API 接口文档

## WorkItem 与依赖

```python
WorkItem(
    id: str,
    agent_id: str,
    objective: str,
    output_key: str,
    dependencies: tuple[WorkItemDependency, ...] = (),
    acceptance_criteria: tuple[str, ...] = (),
    policy_id: str | None = None,
)

WorkItemDependency(
    work_item_id: str,
    source: DependencySource,
    rule_id: str | None = None,
)
```

`id` 区分同一 Trace 中的具体工作项；`agent_id` 选择执行者；`objective` 是动态 task 字符串的核心；`output_key` 指向领域 Agent 的固定项目产物；`dependencies` 定义执行前置条件与来源。重复、自依赖或空字段会被拒绝。

`DependencySource` 包含 `system`、`template`、`planner`。系统依赖由校验策略生成，模板依赖来自 `WorkflowTemplate`，Planner 依赖来自本次草案。

## ExecutionPlan 与 Trace

```python
ExecutionPlan(
    id: str,
    goal: str,
    work_items: tuple[WorkItem, ...],
    template_id: str | None = None,
    trace: TraceContext = ...,
)

ExecutionPlan.work_item(work_item_id: str) -> WorkItem | None
ExecutionPlan.root_items() -> tuple[WorkItem, ...]
```

`ExecutionPlan` 拒绝重复 WorkItem id、未知依赖和循环依赖。直接通过 Template 实例化的计划会获得临时 Trace；经 `PlannerService` 创建的计划使用项目持久化 Trace。

## 模板与执行

```python
TaskBlueprint(...).instantiate() -> WorkItem
WorkflowTemplate(...).instantiate(plan_id: str, goal: str, trace: TraceContext | None = None) -> ExecutionPlan
GraphRunner(agents: AgentRegistry, tools: ToolGateway, *, traces: TraceStore | None = None)
GraphRunner.run(plan: ExecutionPlan) -> GraphRunResult
```

GraphRunner 同步执行 ready WorkItem，并把 Agent 最终文本写入进程内 `RunState`。提供 `TraceStore` 时，还会写入项目私有的 `.projectos/runs/<trace_id>/plan.json` 和 `events.jsonl`。

## TraceStore

```python
TraceStore(project_path: str)
TraceStore.start_trace(goal: str, parent_trace_id: str | None = None) -> TraceContext
```

`TraceContext` 包含稳定 `requirement_id`、单次 `trace_id` 与可选 `parent_trace_id`。Requirement 节点成功后，Store 会在 `.projectos/requirements/` 保存按内容 hash 去重的修订快照。

当前 Trace 记录计划与事件，不提供 RunState 恢复、重试或局部重规划。

# Orchestration API 接口文档

## WorkItem 与计划

```python
WorkItem(id, agent_id, objective, output_key, dependencies=(), acceptance_criteria=())
WorkItemDependency(work_item_id, source, rule_id=None)
ExecutionPlan(id, goal, work_items, template_id=None, trace=...)
```

`DependencySource` 为 `system`、`template` 或 `planner`。`ExecutionPlan` 拒绝重复 WorkItem id、未知依赖和循环依赖。

## 执行与追踪

```python
GraphRunner(agents, tools, *, traces: TraceStore | None = None)
GraphRunner.run(plan) -> GraphRunResult

TraceStore(project_path)
TraceStore.start_trace(goal, parent_trace_id=None) -> TraceContext
```

GraphRunner 调度依赖已满足的 WorkItem，写入进程内 `RunState`。传入 TraceStore 时，它还会写入 `.projectos/runs/<trace_id>/plan.json`、`events.jsonl` 与 Trace 终态；Requirement 内容变动会形成修订快照。

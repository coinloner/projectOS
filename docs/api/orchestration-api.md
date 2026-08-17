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
TraceStore.record_sandbox_evidence(context, result) -> SandboxEvidence
TraceStore.list_sandbox_evidence(context) -> tuple[SandboxEvidence, ...]
TraceStore.load_sandbox_evidence(context, evidence_id) -> SandboxEvidence
```

`ExecutionContext(trace_id, work_item_id, agent_id)` 由 GraphRunner 为每个 WorkItem 创建，再绑定到该 Agent 获得的工具对象。它不属于 LLM task 或工具参数。

GraphRunner 调度依赖已满足的 WorkItem，写入进程内 `RunState`。传入 TraceStore 时，它还会写入 `.projectos/runs/<trace_id>/plan.json`、`events.jsonl`、`evidence/<evidence_id>.json` 与 Trace 终态；Requirement 内容变动会形成修订快照。

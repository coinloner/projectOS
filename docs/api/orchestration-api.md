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
GraphRunner(
    agents,
    tools,
    *,
    traces: TraceStore | None = None,
    artifacts: ArtifactRepository | None = None,
    memory: MemoryStore | None = None,
)
GraphRunner.run(plan) -> GraphRunResult
GraphRunner.run(plan, state=restored_state) -> GraphRunResult

TraceStore(project_path)
TraceStore.start_trace(goal, parent_trace_id=None) -> TraceContext
TraceStore.record_sandbox_evidence(context, result) -> SandboxEvidence
TraceStore.list_sandbox_evidence(context) -> tuple[SandboxEvidence, ...]
TraceStore.load_sandbox_evidence(context, evidence_id) -> SandboxEvidence
```

`GET /api/v1/projects/{project_id}/runs/{trace_id}/progress` 返回监控快照 v3。
它只有三个当前状态层级：

```text
run         Trace 总体生命周期、Worker 进程观察和当前 batch
batches     同一 wave 内一轮 fan-out/fan-in 的屏障状态
work_items  单节点的 lifecycle/outcome/activity、LLM、时钟和证据引用
```

快照不保存事件历史，不复制某个当前 WorkItem 到顶层，也没有 `phase`、
`last_progress_at`、`overall_state` 或 `current` 兼容字段。历史审计仅从
`events.jsonl` 的 `/runs/{trace_id}/events` 读取。API 额外生成只读 `summary`，其中仅包含
active/waiting/completed/failed WorkItem ID、当前 batch 和 `worker_process_state`。
`heartbeat_at` 仅表示 Worker 进程观察，`last_meaningful_at` 才能代表语义或控制面推进。

运行指标可通过 `GET /api/v1/projects/{project_id}/runs/{trace_id}/metrics` 查询。指标由
Trace 追加事件确定性计算，包含事件总数、完成/失败 WorkItem 数、重试次数、重试收敛率和
事件类型计数，不依赖进程内缓存。

`ExecutionContext(trace_id, work_item_id, agent_id)` 由 GraphRunner 为每个 WorkItem 创建，再绑定到该 Agent 获得的工具对象。它不属于 LLM task 或工具参数。

GraphRunner 调度依赖已满足的 WorkItem，写入进程内 `RunState`。传入 TraceStore 时，它还会写入 `.projectos/runs/<trace_id>/plan.json`、`events.jsonl`、`evidence/<evidence_id>.json` 与 Trace 终态；Requirement 内容变动会形成修订快照。传入 `MemoryStore` 后，Runner 会追加 Agent 输入/输出、工具结果和 checkpoint，Agent 输入只读取同一 WorkItem 的有限历史窗口。

运行中断后可以通过 `POST /api/v1/projects/{project_id}/runs/{trace_id}/resume` 恢复最近一次
checkpoint。恢复会重新加载并校验 `plan.json`、Trace 和 RunState；只有已完成 WorkItem 会被跳过，
失败、等待能力或请求重新规划的节点会重新执行。恢复不会修改原始 goal、Workflow 或权限，也不会把
Memory 当作事实来源。

当状态为 `waiting_for_capability_approval` 时，先调用
`GET /api/v1/projects/{project_id}/runs/{trace_id}/capabilities` 查看候选 source，再调用
`POST /api/v1/projects/{project_id}/runs/{trace_id}/capabilities/approve`，请求体为
`{"source_name":"mcp"}`。批准接口会校验 source 是否能满足当前 WorkItem 的 capability，
写入 `capability_approved` 事件，在同一次操作中激活 source 并从 checkpoint 恢复；不会把授权
状态交给 Agent 或依赖进程内缓存。候选按请求 Agent 的 domain 过滤；没有任何注册来源能
满足能力时，运行以 `blocked` 终态结束（例如 docs-mcp 注册于 architecture/code/review
三个 domain，test domain 的同类请求会被转为 setup_failed 证据）。

终态语义：图执行完后解析 Review 结论行「## 审查结论（PASS / CONDITIONAL_PASS / BLOCKED）」，
`BLOCKED` 时运行以 `blocked` 结束且 `error` 说明交付未放行——`blocked` 是可恢复终态，
修复阻塞项后可经 `/runs/{trace_id}/resume` 从 checkpoint 继续。

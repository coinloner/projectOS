# Memory 模块

`app.memory` 是一次 Trace 的运行级会话记忆。它保存用户目标、Planner 草案、Agent 输入/输出、工具结果和
控制面 checkpoint，让无状态 Agent 在同一运行和修复循环中保持连续上下文。

## 边界

- 事件追加到 `.projectos/runs/<trace_id>/memory.jsonl`，完整记录按序保留。
- `Memory` 不替代 Policy（约束和权限）、Artifact（正式交付物）、Trace（控制面事实）或 `RunState`（当前内存状态）。
- Prompt 只通过 `MemoryView` 读取有限窗口；超长内容会截断，权威正文仍从 Trace/Evidence/Artifact 获取。
- Agent 没有任意 Memory 写工具。Runner、Planner 和工具适配层在可信控制面中自动写入事件。
- checkpoint 只供断点恢复和重新规划参考，恢复时仍需重新校验 Plan、Policy、Artifact 和 Trace。

## 分层与生命周期

- `raw`：完整事件日志的默认层，主要用于审计和恢复，不会全部进入 Prompt。
- `temporary`：闲聊、临时草稿或一次性诊断，通常设置过期时间并关闭检索。
- `working`：当前 WorkItem/Trace 的短期上下文，例如最近 Agent 输出和 checkpoint。
- `episodic`：一次运行完成后的摘要或可复用的运行片段。
- `durable`：跨阶段长期候选，必须经过 `propose_durable()` 后再由控制面显式 `promote()`。

Trace 结束时控制面会生成 `run_summary` episodic 事件。摘要是确定性压缩，带有原始事件
`source_refs`；同一 Trace 的新摘要会通过追加状态事件替代旧摘要。

事件还带有 `active/candidate/superseded/expired` 生命周期。候选默认不可检索；过期和替代通过追加控制事件维护，
不会改写原始 JSONL。闲聊可以保留在原始日志中，但默认不建立可召回的长期上下文。

durable candidate 由控制面或人工 Review 维护：`durable_candidates()` 只返回待审批候选，
`promote()` 和 `expire()` 分别追加批准或拒绝状态。后台维护任务可以调用 `cleanup_expired()` 清理到期事件，
但不会删除原始日志。

当前 `MemoryStore.events()`/`view()` 保留结构化顺序窗口，`MemoryStore.search()` 使用可重建的 SQLite/FTS5 索引。
如果传入 `EmbeddingProvider`，同一个 `search()` 会按 65% FTS + 35% 向量相似度进行混合排序；向量 provider
故障会自动回退到 FTS。`MemoryContextAssembler` 按固定预算合并最近工作记忆、checkpoint 和相关召回结果。
RAG 只能召回上下文，不能直接成为事实来源。

`search_durable()` 是唯一的项目级跨 Trace 召回入口，只返回已经 `promote()` 的 durable 事件。
普通 Trace 对话、Agent 输出和候选记忆不会因为语义相似而跨运行泄漏；Planner Repair 可以显式请求这类长期上下文，
普通 Runner 默认关闭。

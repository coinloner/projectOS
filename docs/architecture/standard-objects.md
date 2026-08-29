# 全链路标准化对象

ProjectOS 的对象按边界分层，禁止用同一个 `status` 或 `content` 字符串跨层推断事实。

| 边界 | 标准对象 | 必须表达 | 不负责表达 |
| --- | --- | --- | --- |
| 会话 | `ConversationTurn` | 用户消息、角色、顺序、关联 Trace | 计划状态、产物事实 |
| 运行 | `TraceContext` / `trace.json` | `trace_id`、总目标 `goal`、持久生命周期 | 单节点执行阶段 |
| 规划输入 | `PlanDraft` | LLM 提议的 `ref`、`agent_id`、`objective`、临时依赖 | 权限、路径、工具、可信 node id |
| 可信计划 | `ExecutionPlan` / `WorkItem` | 节点 `id`、`agent_id`、`objective`、依赖、合同和权限 | LLM 原始推理文本 |
| Agent 输入 | `TaskInputPackage` | 项目目标 `project_goal`、节点目标 `objective`、输入引用、作用域、输出合同、验收标准 | 未授权文件正文 |
| Agent 输出 | `AgentResult` | `completed` 或规范化单一 `CapabilityRequest` | 是否可调度、是否交付 |
| 图边界 | `NodeResult` | 节点 id、Agent id、节点状态和失败信号 | Trace 的最终状态 |
| 失败恢复 | `FailurePackage` | 失败证据、修复范围 `repair_scope`、重试约束 | 自动扩大权限 |
| 持久产物 | `ArtifactRef` / ArtifactStore | 产物 key、层级、版本、来源节点 | Agent 临时摘要 |
| 运行监控 | `WorkerProgress` | Worker 存活、LLM call 状态、工具状态、时间戳 | “有 heartbeat 就代表模型有进度” |
| 项目合同 | `ProjectContract` | 分层规则、接口、入口、Wave、文件 owner、测试类型和依赖方向 | 被多个 JSON 合同分别覆盖 |
| 代码交付 | `WorkItem` | ProjectContract 的单文件只读投影 | 用目录 glob 代表完成、跨文件共享写入 |

## 字段语义

- `goal` 只表示一次 Run 的总目标；`objective` 只表示一个 WorkItem 的可执行目标；`project_goal` 是 TaskInputPackage 对 `goal` 的只读投影。
- `output_key` 是控制面状态索引；`artifact_key` 是持久化交付事实；`publish_target` 是发布目的地，三者不能互换。
- `content` 是 Agent 的摘要或节点说明，不是产物事实。质量门必须读取 ArtifactStore、workspace 和证据存储。
- `status` 按层解释：`AgentStatus`、`NodeStatus`、`GraphRunStatus`、Trace 持久状态、Worker `phase` 不能直接比较。
- 代码路径按四层边界解释：目录是授权边界（`allowed_paths`），文件是交付边界（`owned_files`），
  符号是协作边界（`provided_symbols`/`required_symbols`），Wave 是依赖边界；这些字段不能互相替代。
- `CapabilityRequest.capability` 必须是单一 canonical id。多项工具名会在 Agent 边界规范化为能力集合语义（例如 `environment_preparation`），不能拼成待审批来源名。

## LLM 终态协议

```text
llm_request_started
  -> zero or more llm_chunk_received
  -> exactly one llm_completed | llm_failed | llm_cancelled
  -> agent_completed | agent_failed
  -> work_item_completed | work_item_failed
```

`heartbeat` 只证明 Worker 进程存活；`last_progress_at` 只表示最近一次控制面事件。无 chunks 不能判定结束，watchdog 只能在没有 LLM 终态且阶段空闲超过阈值后报告 `provider_stall_timeout`。

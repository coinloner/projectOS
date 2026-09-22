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
| 运行监控 | `MonitorSnapshot v3` | Run、并行 Batch 与 WorkItem 的当前状态；历史事件不复制进快照 | “有 heartbeat/chunk 就代表业务有进展” |
| 恢复参考 | `WorkingState` | 原目标摘要、已确认动作、durable refs、下一步和最近错误类型 | prompt、模型正文、原始思维链或未经验证的完成声明 |
| 项目合同 | `ProjectContract` | 分层规则、接口、入口、Wave、文件 owner、测试类型和依赖方向 | 被多个 JSON 合同分别覆盖 |
| 代码交付 | `WorkItem` | ProjectContract 的单文件只读投影 | 用目录 glob 代表完成、跨文件共享写入 |
| 架构 L0 | `ArchitectureBlueprint` | 系统边界、层级、模块清单和全局约束 | 模块内部文件、具体函数实现 |
| 架构 L1 | `ModuleDesign` | 单模块职责、依赖、实体和协作接口 | 修改总体模块清单、设计其他模块内部 |
| 架构 L2 | `ImplementationDesign` | 接口实现准备、完整文件 ownership、测试边界 | 新增第四层、跨模块重写职责 |
| 架构集成 | `ArchitectureDesignBundle` | 三层对象的 parent、module、interface 和 ownership 一致性 | 重新发明业务需求或直接写代码 |

## 字段语义

- `goal` 只表示一次 Run 的总目标；`objective` 只表示一个 WorkItem 的可执行目标；`project_goal` 是 TaskInputPackage 对 `goal` 的只读投影。
- `output_key` 是控制面状态索引；`artifact_key` 是持久化交付事实；`publish_target` 是发布目的地，三者不能互换。
- `content` 是 Agent 的摘要或节点说明，不是产物事实。质量门必须读取 ArtifactStore、workspace 和证据存储。
- `status` 不属于 v3 监控快照通用字段。监控统一使用 `lifecycle`、`outcome`、`activity` 和 `event_type`；Trace/Node/Graph 的状态枚举仍只在各自边界解释。
- 代码路径按四层边界解释：目录是授权边界（`allowed_paths`），文件是交付边界（`owned_files`），
  符号是协作边界（`provided_symbols`/`required_symbols`），Wave 是依赖边界；这些字段不能互相替代。
- `CapabilityRequest.capability` 必须是单一 canonical id。多项工具名会在 Agent 边界规范化为能力集合语义（例如 `environment_preparation`），不能拼成待审批来源名。
- 架构对象的 `depth` 只允许 `0/1/2`。`ArchitectureBlueprint.design_id` 是 L0 的根；`ModuleDesign.parent_design_id` 必须指向该根；`ImplementationDesign.parent_design_id` 必须指向对应模块设计。`module_id`、`interface_id`、`requirement_ids` 在层间传递时保持同名同义。
- Blueprint 模块的 `purpose` 表示业务价值边界，`responsibility` 表示技术职责，
  `depends_on_modules` 表示模块级 DAG 依赖。第二阶段由 `DynamicPlanBuilder` 将后者编译成
  `architecture_module` WorkItem；模块数量和并行 wave 不再写死在模板中。
- `ArchitectureDesignBundle` 是 Integration 的唯一输入组合。动态线路会在全部
  `ImplementationDesign` 完成后才允许集成；Integration 通过后才生成 Markdown 候选，
  后续 `ProjectContract` 和 `TaskInputPackage` 只能从组合后的事实编译，不读取某个模块的
  自然语言猜测。

## LLM 终态协议

```text
llm_request_started
  -> zero or more llm_chunk_received
  -> exactly one llm_completed | llm_failed | llm_cancelled
  -> agent_completed | agent_failed
  -> work_item_completed | work_item_failed
```

监控快照 v3 的 Run、Batch 和 WorkItem 都使用相同的正交状态语义：

```text
lifecycle: pending | running | waiting | retrying | terminal
activity:  dispatch | worker | llm | tool | artifact | sandbox | integration | approval
outcome:   completed | blocked | failed | cancelled | interrupted | null
```

Run 只记录总体执行与 Worker 进程观察；Batch 记录一个 wave 内的期望成员、完成成员、失败根因和
被连带中断成员；WorkItem 记录当前活动和 LLM 状态。进度事件仍按 `transport`、`semantic`、
`control`、`heartbeat` 分类，但事件历史只写入 `events.jsonl`。监控字段只保留
`last_event_at`、`last_meaningful_at`、`clocks.transport_at`、`heartbeat_at` 与 `terminal_at`；
不再使用 `phase`、`last_progress_at`、`meaningful_progress_at` 或顶层 WorkItem 镜像。

Agent 可通过 `report_progress` 写入最多 240 字的公开工作摘要和下一步，但该事件没有完成权限。
只有成功工具、Artifact、ChangeSet 和 Sandbox 结果可以成为 durable ref。重试时注入的
`WorkingState` 明确标记为恢复参考，不会回放 prompt、流式正文或原始思维链；尚未落盘的模型生成
仍然只能从原 WorkItem 合同重新执行。

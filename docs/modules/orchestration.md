# Orchestration 模块

`app.orchestration` 是 ProjectOS 的运行时编排层：它接收已经验证的计划，按 WorkItem 依赖调度 Agent，并记录本次执行的状态与 Trace。

```text
ExecutionPlan
  -> WorkItem DAG
  -> GraphRunner
  -> RunState + TraceStore
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `WorkItem` | 一次 Trace 中可执行的最小任务：Agent、目标、产物、依赖、完成标准和系统发放的执行授权 |
| `WorkItemDependency` | 一条依赖及来源：system/template/planner |
| `ExecutionPlan` | 本次运行的合法 WorkItem DAG |
| `GraphRunner` | 找到 ready WorkItem，创建 Agent，执行并处理失败/能力缺口 |
| `RunState` | 当前执行结果和 artifact 文本，并可从版本化 checkpoint 重建 |
| `ExecutionContext` | GraphRunner 为单个 WorkItem 创建的可信身份，以及 `execution_mode/input_refs/slot/publish_target` 授权 |
| `TaskInputPackage` | 执行前生成的结构化任务合同：输入用途、资源 scope、输出合同、前置状态、约束和非目标 |
| `TraceStore` | 持久化计划、事件、终态、requirement 修订和 sandbox evidence |
| `SandboxEvidence` | Docker check 的状态、退出码、耗时和原始受限输出，绑定到 Trace 与 WorkItem |
| `ProgressTracker` | 接收 LLM 流式、工具和节点事件，写入 Worker 最近进度快照，不保存模型正文 |

架构动态线路由 `architecture_blueprint` WorkItem 触发控制面扩展：
`DynamicPlanBuilder` 读取已校验的 Blueprint，为每个模块生成独立的
`architecture_module` WorkItem。模块的业务 `purpose` 只作为任务语义传递，
`depends_on_modules` 才会转换为 DAG 依赖；模块节点完成后扩展 provenance 会写入 Trace。

当所有 `architecture_module` 节点完成后，Runner 再触发第二次动态扩展：
`expand_implementations()` 为每个模块生成一个 `architecture_implementation` WorkItem。
这些节点只依赖对应的 ModuleDesign，模块依赖通过输入引用传递，不把未完成的实现设计
强行串成同级依赖。架构集成节点会被控制面补齐实现设计依赖和引用，只有 Blueprint、
ModuleDesign、ImplementationDesign 三层对象齐备后才允许生成 `ArchitectureDesignBundle`。
每次扩展都记录 `kind`、父计划 revision、模块到 WorkItem 映射和 wave，断点恢复直接复用
已持久化的 DAG。

`ExecutionPlan` 同时记录 `process_id`（稳定的流程规则）和可选的 `template_id`（历史或兼容
适配器）。流程规则不等同于某个项目的节点数量；后续动态计划编译会在流程约束下生成项目专属 DAG。

## 边界

- Orchestration 不理解 requirement、代码、测试或 Docker 的领域内容。
- Orchestration 不决定项目应该有哪些工作项，这是 Planner 的职责。
- Orchestration 不保存可复用流程经验，这是 Workflow 的职责。
- Agent 和 Domain Service 不直接修改 WorkItem 状态或 Trace；只有 GraphRunner 与 TraceStore 可以写入。
- `ExecutionContext` 经 ToolGateway 绑定到工具对象，不会写入 Agent task 文本或 ToolDef schema。
- `PARTITIONED` WorkItem 必须有唯一 `slot`，只能写暂存输出；`INTEGRATION` 必须有
  `publish_target`，只能创建候选；`QUALITY_GATE` 必须依赖一个集成 WorkItem，由 GraphRunner
  调用确定性质量门发布。三者均不能由 Planner 的自由文本直接授予。

GraphRunner 使用有界 worker pool 分批执行相互独立的 WorkItem；单批大小由
`PROJECTOS_GRAPH_MAX_WORKERS`（默认 `6`）控制，同一 Agent 的并行数量仍受
`AgentDefinition.max_parallel_instances` 限制。API 进程同时监管的项目数由
`PROJECTOS_RUN_MAX_WORKERS`（默认 `4`）控制。这些是并发 I/O 调度容量，不是让
Agent 取得多进程或宿主机权限。

对 architecture Markdown 的质量门，Runner 不调用 LLM：它加载集成 WorkItem 创建的唯一
候选，检查 `IntegrationReport`，再决定是否调用 `ArtifactRepository.promote_candidate()`。
这让“是否覆盖正式文档”的最终权限始终留在控制面。

Docker 失败会按测试失败、超时、环境配置失败和 Agent 运行异常归因，分别请求 Repair Plan、有限重跑、阻塞或失败。
每个节点结果后都会写入版本化 checkpoint；恢复时只信任已完成节点，失败、等待和 replan 节点重新调度，
避免把中断时的半成品副作用误当成完成结果。

Worker 监管同时使用硬截止、阶段级空闲阈值和独立 heartbeat。默认 LLM 120 秒、工具 300 秒、Sandbox 600 秒；
发现 LLM 无 chunk 时先写入 `worker_idle_suspected`/`provider_stalled`，并进入可配置的
`PROJECTOS_PROVIDER_STALL_GRACE_SECONDS` 宽限期；只有 `last_progress_at` 在整个宽限期内
没有变化才终止 Worker，真实进度恢复会重置计时。API 可通过
`/runs/{trace_id}/progress` 查询 `last_progress_at` 与 `heartbeat_at` 两类时间戳。

`FailurePackage` 是编排层由 Trace evidence 生成的受控输入。Planner 只看到失败种类、摘要、证据 ID、check/runtime/exit code；修复用 Code/Test WorkItem 可看到受长度限制的 stdout/stderr，并且该文本被标记为不可信程序输出。TestAgent 必须产生当前 WorkItem 的 SandboxEvidence，否则不会被记为完成；代码分区先提交 ChangeSet，`CodeIntegrationAgent` 合并成功后生成实现摘要（implementation.md）。

修复计划（携带 failure_package 的 WorkItem）另有落盘闸门：code 域的修复项必须有成功的
`write_workspace_file` 调用记录（由 Trace 记忆中的工具事件判定），否则按
`repair_no_file_change` 重试一次，重试时任务文本会注入「上一轮未落盘」的强制反馈；仍不落盘
则修复项失败而不是假装完成。Planner 的修复计划提示词同样要求修复步骤必须产生实际文件变更，
只读诊断不构成修复步骤。

## 终态判定

图执行完所有 WorkItem 后，Runner 解析 Review 节点的结论行
（固定格式「## 审查结论（PASS / CONDITIONAL_PASS / BLOCKED）」）：

- `PASS` / `CONDITIONAL_PASS` → `completed`（条件记录在 review.md 中）
- `BLOCKED` → `blocked`，交付未放行；`blocked` 属于可恢复终态，修复阻塞项后可
  经 `/runs/{trace_id}/resume` 从 checkpoint 恢复继续交付。

Review 结论缺失或无法解析时按历史行为保持 `completed`。能力等待（`waiting_for_capability_approval`）、
`needs_replan`、`failed` 等中途终态不受 Review 影响，仍按各自路径结束。

## TaskInputPackage

GraphRunner 不再只把 `objective` 拼成一段任务字符串，而是为每次执行生成
`TaskInputPackage`：

| 字段 | 作用 |
|---|---|
| `InputBinding` | 说明 `ArtifactRef` 的用途和读取方式，不注入正文 |
| `TaskScope` | 表达 execution mode、canonical `slot`、允许路径和禁止路径 |
| `OutputContract` | 表达 output/artifact key、发布目标和预期路径 |
| `DependencySummary` | 只传递前置 WorkItem 的状态和产物是否可用 |
| `constraints` / `non_goals` | 把必须遵守的技术边界和明确不做的内容从大段背景中分离出来 |

Agent 收到的是稳定摘要加结构化 JSON；正文仍需通过当前 ToolSet 按 `ref_id` 按需读取。
因此并行 CodeAgent 可以只获得 architecture、environment 和自己的任务合同，不必重复读取
requirement、tasks 或其他分区正文。`TaskInputPackage` 位于
`app/orchestration/task_input.py`，不改变 ToolGateway 的权限判断。

## Architecture 到 CodeAgent 的实现合同

默认交付流程将架构职责拆成两个节点：`architecture_agent` 只产出并发布
`architecture.md`，`architecture_contract_agent` 读取已发布架构并保存
`.projectos/architecture/project-contract.json`。合同节点不修改架构正文，
只负责把架构决策编译为执行边界。

合同中的 `ProjectContract` 同时包含分层规则、依赖方向、禁止导入、路径映射、测试类型、入口、接口和实现单元；
每个 `implementation_unit` 包含层级、目标、依赖、允许/禁止路径、必需文件、验收标准、Policy
和 Skill 引用。`ImplementationContractCompiler` 将这些单元编译为多个 `PARTITIONED`
CodeAgent WorkItem；CodeAgent 只执行当前单元，不重新拆分架构。旧版本在架构 Markdown 中嵌入
标记块的方式仍兼容读取，但新流程由独立合同节点负责生成。

文件交付边界有三条硬规则：

- `allowed_paths`/`allowed_roots` 只表示目录授权，可以使用 glob；
- `owned_files`、`required_paths` 和顶层 `required_files` 只能是具体文件路径；
- 一个 CodeAgent WorkItem 必须且只能拥有一个完整文件。多文件实现单元由编译器按文件拆成
  同一 Wave 的独立 WorkItem，`depends_on` 和接口 owner 依赖会映射到完整的前置子集。

交付门在 ChangeSet 上做精确路径匹配，并按 `provided_symbols` 做最小 AST 符号检查。目录授权
不会再被当成“已经交付”，入口文件也只会要求其唯一 owner，不会注入到 routes、schema 等兄弟
文件任务。这样并行仍发生在 Wave 内，但每个合并单元有清晰的文件责任和可恢复边界。

`project_delivery` 在合同节点完成后由 GraphRunner 自动展开代码子图；不再存在单独的
实现规划入口。各单元仍使用现有 Git task worktree，完成后交给 `CodeIntegrationAgent`
合并，因此并行不会互相覆盖正式 workspace。Policy 的 `preflight()` 提供实现前清单，`evaluate()` 仍负责最终确定性
质量判定；Skill 只提供实现方法，不得绕过 Policy。
# 授权、重试与恢复边界

能力审批会写入每条 Trace 的 `capability-grants.json`，Worker 重建时从授权账本和
`capability_approved` 事件恢复；授权支持 `node`、`trace`、`project` scope，并可通过
`/capabilities/{grant_id}/revoke` 撤销。普通项目未在需求的“外部规范”小节显式声明主题时，
控制面拒绝 `external_documentation` 请求。

失败修复计划携带依赖图局部窗口：失败节点、直接前置节点和直接后继节点。Provider 流式
超过阶段空闲阈值后先记录诊断，继续超过宽限期则终止当前 Worker，并允许从 checkpoint 重试。

## 交付契约与运行前置

`PipelineArtifactManifest`（代码中的 `DeliveryContract`）只为 ProjectOS 自己的最终项目文档声明唯一 owner 和阶段边界；
它不是项目架构合同。它在
`TemplateCompiler` 和 Implementation Contract 展开后校验，防止 `code-integration` 要求
后续 `tests` 或 `review` 才能生成的文件。

`ProjectRuntimePreflight` 在 TestAgent 运行前检查 Compose build context 的 Dockerfile、数据库
初始化入口和前端 HTML 本地资源引用。检查失败返回 `runtime_preflight` 阻塞信号；它不会被 LLM
解释为“测试通过”，修复后可从 checkpoint 恢复。

TestAgent 的 `SandboxEvidence.status=setup_failed` 同样属于环境阻塞，不是完成状态。原始证据仍
持久化，Trace 会停在 `blocked`，避免在没有执行测试的情况下生成误导性的最终 Review。

## 状态机与职责

状态字段按层隔离，不能把不同层的 `status` 直接比较：

```text
LLM call:  idle -> started -> streaming -> completed | failed
WorkItem:  planned -> started -> completed | needs_replan | failed
Graph:     running -> completed | waiting_for_capability_approval
                    -> needs_replan | blocked | failed
Worker:    started -> running -> completed | failed | timed_out
```

`ProgressTracker` 只维护单个 WorkItem 内一次 LLM/工具调用的进度与终态标记；
`GraphRunner` 根据 DAG 依赖和 `NodeResult` 驱动 WorkItem 状态，并把失败归因转换成
有界 retry 或局部 `RepairPlanPatch`；`RunCoordinator` 位于更外层，负责 Worker 线程的
启动、硬截止、Provider stall watchdog、checkpoint 恢复和 API 级运行生命周期。它不参与
节点业务判断，也不替代 GraphRunner 的 DAG 调度。`TraceStore` 是跨进程恢复的事实来源，
`RunState` 是一次 GraphRunner 执行中的内存投影。

## 修复输出协议

每次重试遵循固定的六步：`observe -> classify -> narrow -> act -> verify -> report`。
Agent 必须先读取受控 `FailurePackage`，将失败归类为工具、传输、环境或业务测试问题；
随后只能在 `repair_paths`/`owned_files` 范围内修改，并调用当前 WorkItem 已授权的写入或
测试工具。只有新的工具证据满足输出合同后才可报告完成。Planner 输出 `RepairPlanPatch`
只允许追加 `code_agent`/`test_agent` 修复节点，`repair_scope` 必须与控制面窗口一致，
最多 10 个操作，不能授予新权限、重做未受影响节点或输出业务代码。

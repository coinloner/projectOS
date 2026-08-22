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
| `ExecutionContext` | GraphRunner 为单个 WorkItem 创建的可信身份，以及 `execution_mode/input_refs/output_slot/publish_target` 授权 |
| `TaskInputPackage` | 执行前生成的结构化任务合同：输入用途、资源 scope、输出合同、前置状态、约束和非目标 |
| `TraceStore` | 持久化计划、事件、终态、requirement 修订和 sandbox evidence |
| `SandboxEvidence` | Docker check 的状态、退出码、耗时和原始受限输出，绑定到 Trace 与 WorkItem |

## 边界

- Orchestration 不理解 requirement、代码、测试或 Docker 的领域内容。
- Orchestration 不决定项目应该有哪些工作项，这是 Planner 的职责。
- Orchestration 不保存可复用流程经验，这是 Workflow 的职责。
- Agent 和 Domain Service 不直接修改 WorkItem 状态或 Trace；只有 GraphRunner 与 TraceStore 可以写入。
- `ExecutionContext` 经 ToolGateway 绑定到工具对象，不会写入 Agent task 文本或 ToolDef schema。
- `PARTITIONED` WorkItem 必须有唯一 `output_slot`，只能写暂存输出；`INTEGRATION` 必须有
  `publish_target`，只能创建候选；`QUALITY_GATE` 必须依赖一个集成 WorkItem，由 GraphRunner
  调用确定性质量门发布。三者均不能由 Planner 的自由文本直接授予。

GraphRunner 使用有界 worker pool 执行相互独立的 WorkItem；同一 Agent 的并行数量受 `AgentDefinition.max_parallel_instances` 限制，默认是 `1`。这是并发 I/O 调度，不是让 Agent 取得多进程或宿主机权限。

对 architecture Markdown 的质量门，Runner 不调用 LLM：它加载集成 WorkItem 创建的唯一
候选，检查 `IntegrationReport`，再决定是否调用 `ArtifactRepository.promote_candidate()`。
这让“是否覆盖正式文档”的最终权限始终留在控制面。

Docker 失败会按测试失败、超时、环境配置失败和 Agent 运行异常归因，分别请求 Repair Plan、有限重跑、阻塞或失败。
每个节点结果后都会写入版本化 checkpoint；恢复时只信任已完成节点，失败、等待和 replan 节点重新调度，
避免把中断时的半成品副作用误当成完成结果。

`FailurePackage` 是编排层由 Trace evidence 生成的受控输入。Planner 只看到失败种类、摘要、证据 ID、check/runtime/exit code；修复用 Code/Test WorkItem 可看到受长度限制的 stdout/stderr，并且该文本被标记为不可信程序输出。TestAgent 必须产生当前 WorkItem 的 SandboxEvidence，否则不会被记为完成；CodeAgent 同理必须通过 `save_implementation` 保存实现摘要（implementation.md），否则按 `implementation_summary_missing` 有限重试后失败。

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
| `TaskScope` | 表达 execution mode、output slot、允许路径和禁止路径 |
| `OutputContract` | 表达 output/artifact key、发布目标和预期路径 |
| `DependencySummary` | 只传递前置 WorkItem 的状态和产物是否可用 |
| `constraints` / `non_goals` | 把必须遵守的技术边界和明确不做的内容从大段背景中分离出来 |

Agent 收到的是稳定摘要加结构化 JSON；正文仍需通过当前 ToolSet 按 `ref_id` 按需读取。
因此并行 CodeAgent 可以只获得 architecture、environment 和自己的任务合同，不必重复读取
requirement、tasks 或其他分区正文。`TaskInputPackage` 位于
`app/orchestration/task_input.py`，不改变 ToolGateway 的权限判断。

# 编排字段语义

本文定义运行链路中重复字段的唯一语义。事件和提示词可以重复携带上下文标识，
但不得为同一字段引入第二种含义。

## 权威来源

| 对象 | 职责 | 是否为事实来源 |
| --- | --- | --- |
| `TraceRecord` | 运行身份、需求、项目和计划 lineage | 是 |
| `ExecutionPlan` | DAG 和 WorkItem 集合 | 是 |
| `WorkItem` | 单节点执行合同 | 是 |
| `TaskInputPackage` | Agent Prompt 的只读投影 | 否 |
| `NodeResult` | 节点控制面结果 | 是 |
| `Checkpoint` | 恢复进度和结果引用 | 是 |
| `ArtifactRepository` | 产物正文、ChangeSet、候选和 digest | 是 |
| `FailurePackage` | 失败证据 | 是，但不能覆盖合同 |

## 统一字段

| 旧字段 | 标准字段 | 说明 |
| --- | --- | --- |
| `node_id` | `work_item_id` | 节点执行身份；旧 checkpoint 仅在读取时兼容 |
| `project_goal` | `goal` | 一次 Trace 的目标快照 |
| `output_slot` | `slot` | 分区槽位；仅历史 JSON 读取时兼容旧名 |
| `required_files` | `required_paths` | 实现单元的必交文件；仅 Contract 输入边界兼容旧名 |
| `provides` | `provides_interfaces` | 单元提供的接口 ID 数组 |
| `consumes` | `consumes_interfaces` | 单元消费的接口 ID 数组 |
| `input_artifacts` | `input_refs` | 模板和运行时统一使用结构化引用 |
| `policy_id` / `policy_refs` | `policy_refs` | 统一为策略引用数组；旧 policy_id 仅读取 |
| `failure_context` | `failure_package` / `failure_signal` | 文本只在 Prompt 渲染时临时生成 |

## 不可合并字段

- `artifact_key` 是逻辑产物类别，`output_key` 是节点输出实例，不能混用。
- Architecture 实现设计中的 `provided_interfaces` 是本模块定义并拥有的接口声明，
  `consumed_interfaces` 是对外部接口的引用，不能再放入同一个 `interfaces` 数组。
  前者必须声明 owner，后者不得声明 owner；Integration 根据两组内容生成最终绑定关系。
- Blueprint 的 `purpose` 是模块的业务价值边界，`responsibility` 是技术职责摘要，
  `depends_on_modules` 是模块级依赖。三者不互换：动态计划只用
  `depends_on_modules` 生成 WorkItem 依赖，ModuleDesign 必须原样传递 `purpose`。

- `implementation_units` 只在 depth=2 `ImplementationDesign` 中出现，是文件级实现边界；
  `provided_interfaces` 是本模块拥有的接口定义，`consumed_interfaces` 只能引用其他设计已
  声明的接口。动态实现扩展不会把这些字段交给 Blueprint 或 ModuleDesign 猜测，而是将模块
  级语义和冻结引用交给对应的实现设计节点，最终由 Integration 统一校验。
- `allowed_paths`（授权范围）、`required_paths`（必须产出）和 `owned_files`（文件所有权）
  语义不同，不能互相替代。
- `dependencies` 是真实 DAG 依赖；`dependency_ids` 只能是派生属性；
  `DependencySummary` 只是 Prompt 展示视图。
- `NodeStatus`、`AgentStatus`、`GraphRunStatus`、`ToolResultStatus` 和
  `SandboxStatus` 分属不同层，不能合并成一个全局状态枚举。
- `TaskInputPackage.contract_digest`、`OutputContract.output_kind` 和
  `TaskInputPackage.schema_version` 是 Agent 输入合同的审计字段；Agent 不得自行修改，
  Runner 以 WorkItem 的同名合同为准。

## 持久化规则

- Trace 持久化运行级身份和当前计划引用。
- Plan 持久化节点合同及 DAG 结构。
- Checkpoint 持久化已完成 WorkItem、状态和 Artifact 引用；产物 digest 由 ArtifactRepository 管理，不应把产物正文作为第二事实来源。
- ArtifactRepository 持久化正文和可审计交付物。
- FailurePackage 只能描述失败证据，不能修改 `WorkItem` 的权限、输出类型或所有权。
- 每个 WorkItem 都持有 `contract_digest`。该指纹覆盖 Agent、执行模式、输入引用、DAG 依赖、
  输出类型、允许/禁止路径、必需路径、文件所有权、验收条件和策略/技能引用；恢复或补丁应用前必须重新计算并匹配。
- `allowed_paths` 只能在受控修复中收窄，`forbidden_paths` 只能增加限制；`owned_files`、依赖、
  `output_kind` 和执行模式不可变。局部补丁不能通过修改依赖来绕过原有 DAG。
- 失败恢复统一使用 `RepairPlanPatch`。它只能追加新的修复 WorkItem，依赖只能引用同一补丁中
  更早的修复节点；原计划节点不能被替换、删除或重新授权。`schema_version=1` 是当前唯一可执行版本。

## 迁移约束

新代码只写标准字段。读取历史数据时允许一次性将旧字段映射到标准字段；再次持久化时必须
写回标准字段。任何计划或重试变更都必须重新通过 WorkItem 合同校验，禁止产生允许范围与
禁止范围相交的执行节点。

## 节点执行语义契约

JSON Schema 只保证字段形状，不能保证 Agent 正确理解字段含义。运行时由
`compile_node_contract` 从 `TraceContext`、`ExecutionPlan` 和当前 `WorkItem` 编译
`TaskInputPackage.semantic_contract`。该投影只包含当前节点及其直接前后置节点，不把完整 DAG
或上游正文复制到 Prompt。

语义契约为每个字段声明 `meaning`、`source`、`consumer`、是否必填和 `value_rules`，并声明
节点职责及不可违反的不变量。控制面字段（身份、授权范围、合同指纹、文件所有权）由系统生成，
Agent 只能生成业务语义字段；依赖关系、变更文件和产物 digest 等可从真实状态推导的字段不得由
Agent 自行声称。`validate_task_input_semantics` 在 Worker 启动前检查局部链路、文件所有权和
字段关系，结构合法但语义不合法的输入会在调用模型前被拒绝。

重试沿用同一语义契约，只增加结构化失败证据和允许修改的字段路径，不能通过 Prompt 改写原始合同。

别名映射只发生在 Pydantic/Trace 的输入解析边界；`TaskInputPackage`、`ExecutionContext`、
`WorkItem` 和持久化 Contract 不会同时保留同义字段。若 canonical 字段与别名同时出现且
值不同，输入会被拒绝而不会“择一继续”。

当前 Checkpoint 使用 `artifact_refs` 记录完成节点的产物存在性和归属；旧版 `artifacts` 内联正文
只在读取旧快照时兼容。恢复后的节点通过 ArtifactRepository 获取正文，Checkpoint 中只保留空值占位
用于依赖调度，不作为业务内容来源。

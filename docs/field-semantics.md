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
| `output_slot` | `slot` | 分区槽位；当前内部对象保留旧名以完成迁移 |
| `input_artifacts` | `input_refs` | 模板编译后只使用结构化 ArtifactRef |
| `policy_id` / `policy_refs` | `policy_refs` | 统一为策略引用数组 |
| `failure_context` | `failure_package` / `failure_signal` | 文本只在 Prompt 渲染时临时生成 |

## 不可合并字段

- `artifact_key` 是逻辑产物类别，`output_key` 是节点输出实例，不能混用。
- `allowed_paths`（授权范围）、`required_paths`（必须产出）和 `owned_files`（文件所有权）
  语义不同，不能互相替代。
- `dependencies` 是真实 DAG 依赖；`dependency_ids` 只能是派生属性；
  `DependencySummary` 只是 Prompt 展示视图。
- `NodeStatus`、`AgentStatus`、`GraphRunStatus`、`ToolResultStatus` 和
  `SandboxStatus` 分属不同层，不能合并成一个全局状态枚举。

## 持久化规则

- Trace 持久化运行级身份和当前计划引用。
- Plan 持久化节点合同及 DAG 结构。
- Checkpoint 持久化已完成 WorkItem、状态、Artifact 引用和 digest；不应把产物正文作为第二事实来源。
- ArtifactRepository 持久化正文和可审计交付物。
- FailurePackage 只能描述失败证据，不能修改 `WorkItem` 的权限、输出类型或所有权。

## 迁移约束

新代码只写标准字段。读取历史数据时允许一次性将旧字段映射到标准字段；再次持久化时必须
写回标准字段。任何计划或重试变更都必须重新通过 WorkItem 合同校验，禁止产生允许范围与
禁止范围相交的执行节点。

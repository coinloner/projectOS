# Workflow 模块

`app.workflow` 只保存可复用的流程经验，不参与一次项目的实际执行。

```text
WorkflowTemplate
  -> TaskBlueprint（Agent、目标提示、默认依赖和受控执行合同）
  -> Planner 选择模板
  -> TemplateCompiler 或 PlanValidator 生成实际 WorkItem 图
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `TaskBlueprint` | 描述模板中的一个步骤：Agent、目标、依赖、输入、产物、验收标准、约束和非目标 |
| `WorkflowTemplate` | 一组 `TaskBlueprint` 组成的流程经验 |
| `WorkflowTemplateRegistry` | Planner 可读取的模板目录 |
| `TemplateCompiler` | 将带 `PARTITIONED/INTEGRATION/QUALITY_GATE` 的可信模板展开为一次带授权的 ExecutionPlan |

旧的 `project_delivery_template()` 不再注册为可启动入口。TaskAgent 必须使用
`project_delivery_minimal_template()` 的 PARTITIONED -> INTEGRATION -> QUALITY_GATE 链路。

`architecture_parallel_template()` 是第一个受控模板。Planner 只选择模板，`TemplateCompiler`
完整展开 baseline、API、data、frontend、integration 和 quality gate；模板字段中的执行模式、
slot、输入来源和发布目标不接受 Planner 覆盖。

`architecture_compact_template()` 面向范围明确的小需求。它使用一个短架构决策包、一个
规范化 integration 和 quality gate，避免为了简单任务支付多个平行 scope 的模型成本。
调用方通过 `workflow_id` 显式选择；自动复杂度分类尚未实现。

`project_delivery_minimal_template()` 是当前最小生命周期试点：它复用已发布的中文需求和
架构，执行任务、runtime、backend/frontend 并行代码分区、Policy 合并、Docker 测试和 Review。
CodeAgent 不写正式 workspace，只有确定性 Code Integration 节点通过 Policy 后才能合并。
其中 backend/frontend 只读取 architecture 和 environment；分区差异由
`TaskInputPackage` 的 scope、constraints、non_goals 和验收标准表达。

## 依赖边界

普通模板中的 `depends_on` 是 Blueprint id 间的关系；`PlanningContext` 会把它转换为 Agent 层默认依赖，再由 `DependencyPolicy` 合并为具体 WorkItem 依赖。受控模板由 `TemplateCompiler` 将 Blueprint id 直接转换为 WorkItem id，并生成本次 Trace 的 `ArtifactRef`。

这保证 Workflow 始终是可沉淀、可复用、可阅读的经验资产，而不是运行时状态容器。

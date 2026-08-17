# Workflow 模块

`app.workflow` 只保存可复用的流程经验，不参与一次项目的实际执行。

```text
WorkflowTemplate
  -> TaskBlueprint（Agent、目标提示、默认依赖）
  -> Planner 读取为流程参考
  -> PlanValidator / DependencyPolicy 生成实际 WorkItem 图
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `TaskBlueprint` | 描述模板中的一个建议步骤：Agent、目标提示、默认依赖和预期 artifact |
| `WorkflowTemplate` | 一组 `TaskBlueprint` 组成的流程经验 |
| `WorkflowTemplateRegistry` | Planner 可读取的模板目录 |

`project_delivery_template()` 是当前默认经验：Requirement、Architecture、Task、Bootstrap、Code、Test、Review。它告诉 Planner 常见顺序，但不创建 Agent、不产生 Trace、不维护执行状态，也不直接构造 `ExecutionPlan`。

## 依赖边界

`workflow/` 不 import `app.orchestration/`。模板中的 `depends_on` 是 Blueprint id 间的关系；`PlanningContext` 会把它转换为 Agent 层默认依赖，再由 `DependencyPolicy` 合并为具体 WorkItem 依赖。

这保证 Workflow 始终是可沉淀、可复用、可阅读的经验资产，而不是运行时状态容器。

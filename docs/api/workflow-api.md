# Workflow API 接口文档

## Blueprint 与模板

```python
TaskBlueprint(
    id: str,
    agent_id: str,
    objective: str,
    output_key: str,
    depends_on: tuple[str, ...] = (),
    policy_id: str | None = None,
)

WorkflowTemplate(
    id: str,
    name: str,
    description: str,
    nodes: tuple[TaskBlueprint, ...],
)
```

`TaskBlueprint` 描述模板中的建议步骤，不是可执行 WorkItem。`depends_on` 引用同一 Template 中的 Blueprint id。Template 不携带 Agent 实例、工具、Trace 或运行状态，也不提供直接创建 `ExecutionPlan` 的接口。

## Template Registry

```python
WorkflowTemplateRegistry.register(template: WorkflowTemplate) -> None
WorkflowTemplateRegistry.get(template_id: str) -> WorkflowTemplate | None
WorkflowTemplateRegistry.templates() -> tuple[WorkflowTemplate, ...]
```

Registry 拒绝重复 Template id。Planner 通过 `templates()` 读取完整 Blueprint 与默认依赖，并将它们作为参考经验；实际 WorkItem 计划由 Planner API 和 Orchestration API 协作创建与执行。

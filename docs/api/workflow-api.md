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
    execution_mode: ExecutionMode = ExecutionMode.EXCLUSIVE,
    artifact_key: str | None = None,
    input_artifacts: tuple[str, ...] = (),
    input_from: tuple[str, ...] = (),
    output_slot: str | None = None,
    publish_target: str | None = None,
    candidate_from: str | None = None,
    acceptance_criteria: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    non_goals: tuple[str, ...] = (),
)

WorkflowTemplate(
    id: str,
    name: str,
    description: str,
    nodes: tuple[TaskBlueprint, ...],
)
```

普通 `TaskBlueprint` 描述模板中的建议步骤，`depends_on` 引用同一 Template 中的 Blueprint id。
当 `execution_mode` 为 `PARTITIONED`、`INTEGRATION` 或 `QUALITY_GATE` 时，这些字段组成
模板作者的受控执行合同：

- `PARTITIONED` 必须指定 `output_slot`，只能写暂存输出。
- `INTEGRATION` 必须指定 `publish_target`，`input_from` 指向要整合的分区。
- `QUALITY_GATE` 必须指定 `publish_target` 和 `candidate_from`，由 Runner 执行发布。

`acceptance_criteria` 是可验证完成标准；`constraints` 是本节点必须遵守的技术、范围或
权限边界；`non_goals` 是明确不应在本节点实现的内容。Runner 会将三者连同输入引用用途、
前置节点状态和输出合同生成 `TaskInputPackage`，避免把完整上游文档重复塞入 Agent prompt。

Planner 只能选择受控模板，不能在 PlanDraft 中填写这些字段。`TemplateCompiler` 在真实
Trace 下生成 WorkItem、暂存引用和质量门依赖。

## Template Registry

```python
WorkflowTemplateRegistry.register(template: WorkflowTemplate) -> None
WorkflowTemplateRegistry.get(template_id: str) -> WorkflowTemplate | None
WorkflowTemplateRegistry.templates() -> tuple[WorkflowTemplate, ...]
```

Registry 拒绝重复 Template id。Planner 通过 `templates()` 读取完整 Blueprint 与默认依赖，并将它们作为参考经验；实际 WorkItem 计划由 Planner API 和 Orchestration API 协作创建与执行。

```python
TemplateCompiler().compile(
    architecture_parallel_template(),
    goal="设计系统架构",
    plan_id="architecture-plan",
    trace=trace,
    agent_output_keys={"architecture_agent": "architecture"},
)
```

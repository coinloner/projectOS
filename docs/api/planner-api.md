# Planner API 接口文档

## 不可信草案模型

```python
PlannedStep(
    ref: str,
    agent_id: str,
    objective: str,
    depends_on: list[str] = [],
    acceptance_criteria: list[str] = [],
)

PlanDraft(
    rationale: str,
    steps: list[PlannedStep],
    template_hint_id: str | None = None,
    template_dependency_overrides: list[TemplateDependencyOverride] = [],
)
```

`ref` 只在一份草案内引用其他 step，最终 WorkItem id 由 `PlanValidator` 生成。Planner 不能生成可信 trace/work item 身份、产物 key、文件路径、工具或可执行对象。

对于受控 Workflow（当前为 `architecture_parallel`），Planner 只需要选择
`template_hint_id`，`steps` 可以为空。系统随后通过 `TemplateCompiler` 使用模板作者声明的
执行合同生成完整 WorkItem DAG。Planner 不拥有 `execution_mode`、`output_slot`、`input_refs`、
`publish_target` 或质量门候选来源的写权限。

`TemplateDependencyOverride` 只能移除已选择 Agent 间真实存在的模板默认依赖，并必须提供原因；它不能移除系统依赖。

## PlanningContext

Planner 可读取目标、artifact 是否存在、Agent 合同摘要、完整模板节点与默认依赖、workspace 实现文件数量和
`RuntimeSnapshot`。运行级 Memory 不注入普通首次规划的控制面上下文；它会记录 goal 和 Planner 输出，并在
`plan_repair()` 中读取同一 Trace 的有限历史窗口。Memory 只是连续性参考，不是事实来源；Planner 不拥有任意写权限。

## PlannerService 与依赖策略

```python
PlannerService(
    runtime: PlannerRuntime,
    agents: AgentRegistry,
    templates: WorkflowTemplateRegistry,
    artifacts: ArtifactStore,
    validator: PlanValidator | None = None,
    traces: TraceStore | None = None,
)

PlannerService.plan(goal: str, plan_id: str) -> PlannerResult
```

`plan()` 先建立项目 Trace，再调用 LLM 生成草案。`PlanValidator` 验证 schema、Agent、临时 ref 和 DAG，并由 `DependencyPolicy` 合并：

1. Planner 显式依赖。
2. 当前 Template 对已选择步骤的默认依赖。
3. 系统强制依赖，例如无实现时 `Code -> Test -> Review`、无 runtime 时 `Bootstrap -> Code/Test`。

非法草案会带校验错误重试一次；第二次仍非法则抛出 `PlannerFailure`。同一 Agent 可在一份计划中多次出现，但每个 step ref 必须唯一；多个同类前置节点时，Planner 必须用 `depends_on` 明确选择依赖。

`PlannerService.plan_repair(previous_plan, failure, plan_id)` 使用可信失败类型、证据 ID、错误摘要和历史计划摘要生成同一 Trace 内的新计划。它只能追加新的 WorkItem，不能修改历史节点、工具权限或项目文件。

修复计划中属于 code/test domain 的 WorkItem 会由系统附加 `FailurePackage`。Planner 不读取原始测试输出；`FailurePackage` 仅向修复节点提供受限 stdout/stderr 摘要，并将它声明为不可信诊断数据。

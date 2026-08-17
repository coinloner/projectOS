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

`TemplateDependencyOverride` 只能移除已选择 Agent 间真实存在的模板默认依赖，并必须提供原因；它不能移除系统依赖。

## PlanningContext

Planner 可读取目标、artifact 是否存在、Agent 合同摘要、完整模板节点与默认依赖、workspace 实现文件数量和 `RuntimeSnapshot`。它不读取 artifact/源码正文、factory、Tool、MCP client 或 Docker 配置。

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

非法草案会带校验错误重试一次；第二次仍非法则抛出 `PlannerFailure`。Planner v0 仍限制同一 Agent 在一份初始计划中只出现一次。

# Planner API 接口文档

## 不可信草案模型

```python
PlannedStep(agent_id: str, objective: str, depends_on: list[str] = [])
PlanDraft(rationale: str, steps: list[PlannedStep], template_hint_id: str | None = None)
PlanDraft.parse(content: str) -> PlanDraft
```

`PlanDraft` 是 LLM 返回的窄 JSON schema。它不能包含 node id、output key、文件路径、工具或可执行对象；未知字段、空字符串和超出长度限制会被 Pydantic 拒绝，并以 `PlanDraftError` 对外报告。

## PlanningContext

```python
PlanningContext.build(
    goal: str,
    agents: AgentRegistry,
    templates: WorkflowTemplateRegistry,
    artifacts: ArtifactStore,
) -> PlanningContext

PlanningContext.as_prompt_json() -> str
```

规划上下文只包含目标、artifact 是否存在、Agent 合同摘要、Template 摘要、workspace 实现文件数量和 `RuntimeSnapshot`。它不包含 artifact 或源码正文、factory、Tool、MCP client 或 Docker 配置。

## PlannerService

```python
PlannerService(
    runtime: PlannerRuntime,
    agents: AgentRegistry,
    templates: WorkflowTemplateRegistry,
    artifacts: ArtifactStore,
    validator: PlanValidator | None = None,
)

PlannerService.plan(goal: str, plan_id: str) -> PlannerResult
```

`plan()` 调用 `PlannerRuntime.generate()` 生成草案，再由 `PlanValidator` 转换为 `ExecutionPlan`。非法草案会带校验错误重试一次；第二次仍非法则抛出 `PlannerFailure`。

`PlanValidator` 当前拒绝未知/重复 Agent、未知模板、非法依赖与循环图；在 workspace 尚无实现时还会要求 Code、Test、Review 的必要前置依赖，并在 runtime 缺失时要求 Bootstrap。

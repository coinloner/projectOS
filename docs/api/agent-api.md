# Agent API 接口文档

## BaseAgent

```python
BaseAgent(
    gateway: ToolGateway,
    domain: str,
    role: str,
    goal: str,
    backstory: str,
    max_iterations: int = 10,
) -> BaseAgent

BaseAgent.run(task: str) -> AgentResult
```

`run()` 创建一个 CrewAI Agent 和 Task，并只注入 `ToolGateway.tools_for(domain)` 返回的已授权工具。空 task 或 CrewAI 执行失败时抛出 `RuntimeError`。

## AgentResult

| `status` | 字段 | 含义 |
|---|---|---|
| `completed` | `content` | 节点完成后的最终文本 |
| `needs_capability` | `capability_request.capability`、`reason` | 需要未提供的外部能力 |

严格的 capability request JSON 才会被解析为 `needs_capability`。Agent 不会自行连接或授权 MCP。

## AgentRegistry

```python
AgentDefinition(id: str, domain: str, description: str, output_key: str)
AgentRegistry.register(definition: AgentDefinition, factory: AgentFactory) -> None
AgentRegistry.definition(agent_id: str) -> AgentDefinition | None
AgentRegistry.definitions() -> tuple[AgentDefinition, ...]
AgentRegistry.create(agent_id: str) -> AgentRunner
```

`register()` 拒绝重复 Agent id 和重复 output key。Planner 只能读取 `definition()` / `definitions()`；只有 GraphRunner 可以调用 `create()`，使 `ExecutionPlan` 不携带工厂、Python 类或工具。

## 已注册 Agent

所有领域 Agent 的构造函数形式一致：

```python
RequirementAgent(gateway: ToolGateway)
ArchitectureAgent(gateway: ToolGateway)
TaskAgent(gateway: ToolGateway)
BootstrapAgent(gateway: ToolGateway)
CodeAgent(gateway: ToolGateway)
TestAgent(gateway: ToolGateway)
ReviewAgent(gateway: ToolGateway)
```

它们均继承 `BaseAgent.run(task) -> AgentResult`，差异只在 domain、CrewAI 角色提示和最大迭代数。具体可用工具见 [Domain API](domain-api.md)。

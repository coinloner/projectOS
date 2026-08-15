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
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `gateway` | `ToolGateway` | 是 | 外部注入的工具授权和 CrewAI 适配入口 |
| `domain` | `str` | 是 | Agent 对应的工具域 |
| `role` | `str` | 是 | CrewAI Agent 角色 |
| `goal` | `str` | 是 | CrewAI Agent 目标 |
| `backstory` | `str` | 是 | CrewAI Agent 的领域约束与工作方式 |
| `max_iterations` | `int` | 否 | 最大 tool-calling 轮数 |

### run

```python
BaseAgent.run(task: str) -> AgentResult
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | `str` | 是 | GraphRunner 组装的节点任务描述 |

返回结构化 `AgentResult`。

| `status` | 含义 | 可用字段 |
|---|---|---|
| `completed` | Agent 已完成当前任务 | `content` |
| `needs_capability` | 当前可见工具缺少关键外部能力 | `capability_request.capability`、`capability_request.reason` |

`needs_capability` 只在 LLM 返回严格能力请求 JSON 时产生；Agent 不会自行激活 MCP。

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | `task` 为空 |
| `RuntimeError` | CrewAI 执行失败或模型调用失败 |

## RequirementAgent

```python
RequirementAgent(gateway: ToolGateway) -> RequirementAgent
```

- 固定 `domain="requirement"`
- 固定 `max_iterations=5`
- 继承 `BaseAgent.run(task) -> AgentResult`

## 契约

1. 所有 Agent 暴露统一入口 `run(task: str) -> AgentResult`。
2. Agent 不直接注册工具。
3. Agent 不控制 MCP，也不参与远端工具授权。
4. 一个 Agent 类型对应一个 domain。
5. 工具调用由 CrewAI 负责，Agent 只能经由 `ToolGateway.tools_for()` 获得工具。

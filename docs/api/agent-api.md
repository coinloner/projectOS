# Agent API 接口文档

## BaseAgent

```python
BaseAgent(
    manager: ToolManager,
    domain: str,
    system_prompt: str,
    max_iterations: int = 10,
) -> BaseAgent
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `manager` | `ToolManager` | 是 | 外部注入的工具管理器 |
| `domain` | `str` | 是 | Agent 对应的工具域 |
| `system_prompt` | `str` | 是 | Agent 角色定义 |
| `max_iterations` | `int` | 否 | 最大 tool-calling 轮数 |

### run

```python
BaseAgent.run(task: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | `str` | 是 | Workflow 传入的任务描述 |

返回 LLM 的最终文本结果。

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | `task` 为空 |
| `RuntimeError` | 超过最大迭代次数 |

## RequirementAgent

```python
RequirementAgent(manager: ToolManager) -> RequirementAgent
```

- 固定 `domain="requirement"`
- 固定 `max_iterations=5`
- 继承 `BaseAgent.run(task) -> str`

## 契约

1. 所有 Agent 暴露统一入口 `run(task: str) -> str`。
2. Agent 不直接注册工具。
3. Agent 不控制 external，也不参与远端工具授权。
4. 一个 Agent 类型对应一个 domain。
5. 工具调用必须经由 `ToolManager.call()`。

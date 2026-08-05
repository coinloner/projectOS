# Agent API 接口文档

> **定位**：定义 Agent 层对外的公共契约。所有 Agent 实现必须遵循此文档中声明的签名和异常语义。

## 1. BaseAgent

### 构造函数

```python
BaseAgent(
    registry: ToolRegistry,
    system_prompt: str,
    max_iterations: int = 10,
) -> BaseAgent
```

| 参数 | 类型 | 默认值 | 必填 |
|---|---|---|---|
| `registry` | `ToolRegistry` | — | 是 |
| `system_prompt` | `str` | — | 是 |
| `max_iterations` | `int` | `10` | 否 |

- `registry` 由外部注入，Agent 不管理注册
- `system_prompt` 定义 Agent 角色和行为规范

| 异常 | 触发条件 |
|---|---|
| 无 | 构造函数不抛异常 |

---

### 执行

```python
BaseAgent.run(task: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | `str` | 是 | Workflow 传入的任务描述 |

### 返回值

`str` —— LLM 最终文本回复（工具执行完成后）

### 异常

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | `task` 为空或全空白字符 |
| `RuntimeError` | Agent 超过 `max_iterations` 仍未结束 |

### 执行契约

1. **`run()` 是单次执行** —— 不维护跨调用状态，每次调用独立
2. **LLM 自主决定工具调用** —— Agent 不预设调用顺序，不硬编码 tool 调用逻辑
3. **工具通过 Registry 调用** —— 不直接 import Tool 模块

---

## 2. RequirementAgent

### 构造函数

```python
RequirementAgent(registry: ToolRegistry) -> RequirementAgent
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `registry` | `ToolRegistry` | 是 | 已注册了 requirement 相关工具的 Registry |

- 内置 system prompt（需求分析师角色）
- `max_iterations` 固定为 5

### 执行

```python
RequirementAgent.run(task: str) -> str
```

与 `BaseAgent.run()` 签名一致。`task` 为用户的自然语言需求描述。

---

## 3. 扩展 Agent 规范

新增 Agent 时遵循以下契约：

```python
class NewAgent(BaseAgent):
    def __init__(self, registry: ToolRegistry, ...):
        super().__init__(
            registry=registry,
            system_prompt="...",  # 角色定义
        )
```

要求：

1. **继承 `BaseAgent`** —— 不要重新实现 tool-calling loop
2. **`registry` 作为构造参数** —— 不在 Agent 内部调用 `register()`
3. **`run(task) -> str` 签名不变** —— Workflow 调用的统一入口

---

## 兼容性约定

1. **`run(task) -> str` 签名不可变** —— 所有 Agent 的统一入口
2. **不直接调用 Tool 模块** —— 通过 `ToolRegistry.call()` 间接调用
3. **不维护会话状态** —— 会话记忆是 Memory 模块的职责
4. **Agent 内不硬编码工具列表** —— 工具由外部注册，Agent 通过 `list_tools()` 动态发现

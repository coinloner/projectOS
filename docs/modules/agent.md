# Agent 模块

## 概述

`app.agent` 是 ProjectOS 的 Agent 层 —— Workflow 与 Tool 之间的智能执行节点。每个 Agent 接收一个 task，通过 LLM 自主决定调用哪些 Tool、调几次，最后返回结果。

## 架构定位

```
Workflow → Agent → ToolRegistry → Tool
              ↑
          BaseAgent
```

Agent 不管理工具注册，不感知 provider 消息格式，不维护跨 `run()` 的会话状态。

## 依赖

| 模块 | 用途 |
|---|---|
| `app.llm.llm_client` | LLMClient + LLMResponse |
| `app.tool_registry.registry` | ToolRegistry — 工具发现与调用 |

## 类设计

### `BaseAgent`

```
BaseAgent
├── __init__(registry, system_prompt, max_iterations?)
└── run(task) -> str                      # tool-calling loop
```

### `RequirementAgent`

```
RequirementAgent(BaseAgent)
└── __init__(registry)
```

---

## BaseAgent

### 职责边界

| 负责 | 不负责 |
|---|---|
| 接收 task | 工具注册 → ToolRegistry |
| 通过 Registry 发现可用工具 | Provider 格式 → LLMClient.build_*() |
| LLM 自主决定调哪些 Tool | 跨节点编排 → Workflow |
| 执行 Tool 并喂回结果 | 跨 Workflow → Planner |
| 返回执行结果 | 会话记忆 → Memory |
| | Prompt 模板 → Policy |

### `__init__(registry, system_prompt, max_iterations=10)`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `registry` | `ToolRegistry` | 是 | 工具注册中心（外部注入） |
| `system_prompt` | `str` | 是 | 系统提示词，定义 Agent 角色 |
| `max_iterations` | `int` | 否 | 最大 tool-calling 迭代次数，默认 10 |

### `run(task: str) -> str`

Agent 的唯一入口。Workflow 调用此方法。

**执行流程：**

```
1. 构建 messages = [system_prompt, user_task]
2. 通过 registry.list_tools() 获取可用工具列表
3. LLM 请求 → LLMResponse
   ├── 无 tool_calls → 返回 response.content
   └── 有 tool_calls →
        ├── llm.build_assistant_message() → 拼 provider 消息
        ├── registry.call() → 执行工具
        ├── llm.build_tool_result() → 拼结果消息
        └── 循环回到步骤 3
4. 超过 max_iterations → RuntimeError
```

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | task 为空 |
| `RuntimeError` | 超过最大迭代次数 |

---

## RequirementAgent

### `__init__(registry: ToolRegistry)`

继承 `BaseAgent`，内置需求分析师的 system prompt。不管理工具注册 —— registry 由调用方注入。

### System Prompt

- 角色：需求分析师
- 工作流程：接收描述 → 生成需求文档 → 调用 `save_requirement` 保存
- 修改流程：调用 `load_requirement` 读取 → 修改 → 保存
- 原则：不添加用户没提到的功能

---

## 使用示例

```python
from app.tool_registry.registry import ToolRegistry
from app.requirement.requirement_tool import RequirementToolSet
from app.agent.requirement_agent import RequirementAgent

# 启动时集中注册工具（方案 A）
tools = RequirementToolSet("./projects/MyProject")
registry = ToolRegistry()
registry.register("save_requirement", tools.save, "保存需求文档", {...})
registry.register("load_requirement", tools.load, "读取需求文档", {...})

# Agent 注入 registry
agent = RequirementAgent(registry)
result = agent.run("我要一个博客系统")
```

---

## 设计原则

- **Registry 注入，不管理注册**：Agent 接收已注册好的 Registry，不调用 `register()`
- **单节点执行**：每次 `run()` 是独立的任务节点，不维护跨调用状态
- **LLM 自主决策**：Agent 不预设调用顺序，LLM 根据 system prompt 自行决定调哪些 Tool
- **Provider 无关**：拼消息通过 `LLMClient.build_*()` 完成，Agent 不碰 provider 格式

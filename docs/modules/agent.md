# Agent 模块

## 概述

`app.agent` 是 ProjectOS 的 Agent 层。Agent 是 Workflow 中的单节点智能执行单元：接收一个 task，通过 LLM 自主决定是否调用工具，并返回最终结果。

## 架构定位

```
Workflow → Agent → ToolManager → ToolRegistry / External Source → Tool
```

Agent 不管理工具注册，不参与工具暴露半径决策，不维护跨 `run()` 的会话状态。

## 类设计

```
BaseAgent
├── __init__(manager, domain, system_prompt, max_iterations?)
└── run(task) -> str

RequirementAgent(BaseAgent)
└── __init__(manager)
```

## BaseAgent 职责

| 负责 | 不负责 |
|---|---|
| 接收单次 task | 工具注册 |
| 调用 ToolManager 获取当前可用工具 | 本地 ToolSet 注册、external 激活 |
| 调用 LLM 并处理 tool_calls | Workflow 编排 |
| 执行工具并把结果喂回 LLM | 会话记忆 |
| 返回最终文本结果 | Prompt 模板管理 |

## 执行流程

```
1. 构建 system + user messages
2. manager.list_tools(domain)
3. llm.invoke(messages, tools)
   ├── 无 tool_calls → 返回 response.content
   └── 有 tool_calls
        ├── llm.build_assistant_message()
        ├── manager.call(name, arguments, domain)
        ├── llm.build_tool_result()
        └── 继续循环
```

## RequirementAgent

`RequirementAgent` 固定使用 `domain="requirement"`，内置需求分析师 system prompt。

当前流程：

1. 用户输入自然语言需求
2. LLM 生成结构化需求文档
3. LLM 调用 `save_requirement`
4. 工具写入 `requirement.md`
5. Agent 返回最终文本

确认、修改、多轮状态机后续由 `Workflow` 和 `Memory` 实现。

## 使用示例

```python
from app.agent.requirement_agent import RequirementAgent
from app.tool_manager.manager import ToolManager
from app.tool_manager.source import ToolDef, ToolSetSource

manager = ToolManager()
manager.register_toolset(
    domain="requirement",
    name="base",
    source=ToolSetSource([
        (ToolDef("save_requirement", "保存需求文档", {...}), save_fn),
    ]),
)

agent = RequirementAgent(manager)
result = agent.run("我要一个博客系统")
```

## 设计原则

- Agent 只消费 ToolManager，不直接依赖 ToolRegistry。
- Agent 不控制 external，不决定是否接入远端工具。
- 一个 Agent 类型对应一个 domain。
- Provider 消息格式由 LLMClient 处理。

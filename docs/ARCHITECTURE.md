# ProjectOS 架构

## 分层架构

```
Workflow    →  流程状态机（多轮交互：确认 → 修改 → 保存）
  Agent     →  智能执行节点（单次 task → 自主调 Tool → 返回结果）
    Tool    →  原子能力（文件读写、命令执行、LLM 调用）
```

## 层级职责

| 层 | 职责 | 不负责 |
|---|---|---|
| **Planner**（未来） | 跨 Workflow 规划和调整 | 单节点执行 |
| **Workflow**（未来） | 单流程状态机，管理多轮对话 | 单节点智能决策 |
| **Agent** | 接收 task → LLM 自主决定调哪些 Tool → 返回结果 | 工具注册、跨节点编排、会话记忆 |
| **ToolRegistry** | 工具注册、发现、执行 | Agent 业务逻辑 |
| **Tool** | 单一原子操作 | 决策、编排 |

## 当前模块

```
app/
├── agent/                    # Agent 层
│   ├── base_agent.py         # BaseAgent — 智能执行节点基类
│   └── requirement_agent.py  # RequirementAgent — 需求分析
├── tool_registry/            # 工具注册中心
│   └── registry.py           # ToolRegistry — 注册 / 发现 / 调用
├── llm/                      # LLM 客户端
│   ├── config.py             # 厂商预设、激活厂商
│   └── llm_client.py         # LLMClient + LLMResponse
├── requirement/              # 需求文档 Tool
│   ├── requirement.py        # Requirement — 底层文件操作
│   └── requirement_tool.py   # RequirementToolSet — Agent 可调用的工具集
├── runtime/                  # 命令执行 Tool
│   └── Runtime.py            # Runtime.run()
└── project/                  # 项目管理 Tool
    └── project.py            # Project — 创建/加载/删除/扫描
```

## 调用链路

```
main.py（方案 A：启动时集中注册工具）
  │
  ├── registry = ToolRegistry()
  ├── registry.register("save_requirement", ...)
  ├── registry.register("load_requirement", ...)
  │
  └── agent = RequirementAgent(registry)
       │
       agent.run("我要一个博客系统")
         │
         ├── registry.list_tools()                  → "有什么工具可用？"
         ├── llm.invoke(messages, tools)             → LLMResponse
         │     ├── response.is_tool_call == True     → LLM 请求调 tool
         │     └── response.is_tool_call == False    → LLM 返回文本（结束）
         ├── llm.build_assistant_message(tool_calls) → provider 格式
         ├── registry.call(name, arguments)          → 执行工具
         └── llm.build_tool_result(call_id, result)  → 结果消息
              │
              ▼
         返回结果给 Workflow
```

## 关键设计决策

### Tool 注册：方案 A（启动时集中注册）

工具在启动入口统一注册，Agent 不管理注册。将来切换到 MCP 动态发现时，只改 Registry 内部实现。

### Tool Calls 格式：方案 B（简化格式）

`LLMResponse.tool_calls` 使用 provider 无关的简化格式 `[{"id", "name", "arguments"}]`。拼 provider 消息时由 `LLMClient.build_*()` 完成转换，换模型只改这两个方法。

### 不提前实现的模块

| 模块 | 职责 | 当前状态 |
|---|---|---|
| Workflow | 流状态机，多轮对话 | 未实现 |
| Memory | 会话记忆，跨 run() 持久化 | 未实现 |
| Policy | Prompt 模板管理 | 未实现 |
| Planner | 跨 Workflow 规划 | 未实现 |
| ContextBuilder | Context 拼接 | 未实现 |

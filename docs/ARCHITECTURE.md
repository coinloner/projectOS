# ProjectOS 架构

## 分层架构

```
Planner（未来）       跨 Workflow 规划和调整
  ↓
Workflow（未来）      流程状态机，负责确认、修改、external 授权、shell 人工确认
  ↓
Agent                单节点智能执行，调用 LLM 并使用工具
  ↓
ToolManager          提供本地 ToolSet，控制 external
  ↓
ToolRegistry         本地 ToolSet 的持久存储和调用入口
  ↓
ToolSource / Tool    ToolSet 本地工具或 external 远端工具
```

## 当前模块

```
app/
├── agent/
├── tool_manager/
├── tool_registry/
├── requirement/
├── llm/
├── runtime/
└── project/
```

## 工具暴露模型

### 本地 ToolSet

本地工具以 ToolSet 为单位注册，默认暴露给对应 domain 的 Agent。

```python
manager.register_toolset("requirement", "base", ToolSetSource(...))
```

本地不做动态扫描。工具候选集大小由 ToolSet 设计控制。

### External Source

External 面向 MCP 等远端动态工具，默认锁住：

```python
manager.register_source("requirement", "mcp", ExternalDynamicSource())
manager.enable_external("requirement")
manager.activate_external("requirement")
```

External 工具每次 `list_tools()` 重新 discover，不写入 ToolRegistry。

### Shell

Shell 不作为普通 Tool 暴露。未来 Workflow 在人工确认后调用：

```python
Runtime.run_checked(command, cwd, allowed_commands)
```

## 调用链路

```
main.py
  ├── manager = ToolManager()
  ├── manager.register_toolset("requirement", "base", ToolSetSource(...))
  └── agent = RequirementAgent(manager)
       │
       agent.run(task)
         ├── manager.list_tools("requirement")
         ├── llm.invoke(messages, tools)
         ├── manager.call(name, arguments, "requirement")
         └── 返回最终文本给 Workflow
```

## 职责边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Agent | 单次 task 执行、LLM tool-calling loop | 工具注册、external 授权、shell 权限 |
| ToolManager | 本地 ToolSet 暴露、external 开关 | shell、人类确认 |
| ToolRegistry | 存储本地 ToolSet 工具，委托 source 执行 | 暴露半径决策 |
| ToolSource | discover + execute | Agent 业务逻辑 |
| Runtime | 命令执行和基础命令校验 | 是否允许执行命令 |
| Workflow | 流程状态、确认、升 external、shell 人工授权 | 底层命令执行 |

## 关键设计决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 本地工具 | 不动态扫描，以 ToolSet 为单位注册 | 本地能力是已知工程能力，重点是设计好 ToolSet |
| External | enable + activate 两步 | 防止跳过本地工具直接接入远端动态工具 |
| ToolDef | 不包含 `fn` | 兼容 MCP，执行逻辑归 Source |
| Shell | 不作为自动 Tool | 必须人工确认后经 Runtime 执行 |

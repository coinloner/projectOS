# ToolManager 模块

## 概述

`app.tool_manager` 是 ProjectOS 的工具暴露控制层。它位于 Agent 和 ToolRegistry 之间，向 Agent 提供当前 domain 可用的工具。

当前设计只有两类来源：

- **本地 ToolSet**：项目内已知工具，按 ToolSet 单位注册，默认暴露。
- **External Source**：外部动态工具来源，面向 MCP，默认锁住。

本地不做动态扫描。工具暴露半径由 ToolSet 的设计质量控制。

## 架构定位

```
Agent
  │
  ▼
ToolManager
  ├── 本地 ToolSet      # 持久注册，默认暴露
  ├── External Source   # enable + activate 后动态 discover
  └── ToolRegistry      # 本地 ToolSet 的存储和调用入口
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `ToolDef` | 描述工具：name / description / parameters |
| `ToolSetSource` | 承载本地 ToolSet，保存 `ToolDef -> Callable` 映射 |
| `ExternalDynamicSource` | 外部动态工具来源，占位给 MCP |
| `ToolManager` | 注册本地 ToolSet、控制 external、向 Agent 提供工具 |

## 生命周期

| 来源 | 暴露方式 | 存储 | 生命周期 |
|---|---|---|---|
| 本地 ToolSet | 注册后默认暴露 | ToolRegistry | session 持久 |
| External Source | `enable_external()` + `activate_external()` 后暴露 | 临时缓存 | 单次 `list_tools()` |

## Shell

Shell 不属于 ToolManager。命令行能力由未来 Workflow 在人工确认后调用 `Runtime.run_checked()` 完成。

```
Agent 返回需要命令
  ↓
Workflow 请求用户确认
  ↓
Runtime.run_checked(...)
```

## 设计原则

- 本地工具不扫描，以 ToolSet 为单位注册。
- Agent 不参与 external 激活。
- External 默认锁住，避免跳过本地工具直接接入远端工具。
- `ToolDef` 不包含 `fn`，执行逻辑归 `ToolSource.execute()`。

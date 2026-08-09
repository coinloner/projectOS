# ProjectOS Roadmap

## 当前系统架构

```
Planner □
  ▼
Workflow □
  ▼
RequirementAgent ✅
  ▼
ToolManager ✅
  ├── Local ToolSet ✅        本地已知工具，默认暴露
  └── External Source ◇       MCP 等远端动态工具，默认锁住
  ▼
ToolRegistry ✅
  ▼
Tool ✅
  ├── RequirementToolSet ✅
  ├── Runtime ✅
  └── LLMClient ✅
```

说明：

- `✅` 已实现并跑通
- `◇` 接口已预留，真实 MCP connector 后续实现
- `□` 未来模块

## 当前已完成

| 模块 | 文件 | 状态 |
|---|---|---|
| Project | `app/project/project.py` | ✅ 项目创建、加载、删除、扫描 |
| Runtime | `app/runtime/Runtime.py` | ✅ `run()` + `run_checked()` |
| Requirement | `app/requirement/requirement.py` | ✅ `requirement.md` 读写 |
| RequirementToolSet | `app/requirement/requirement_tool.py` | ✅ save/load 工具封装 |
| LLMClient | `app/llm/llm_client.py` | ✅ `LLMResponse` + tool calls |
| ToolRegistry | `app/tool_registry/registry.py` | ✅ 本地工具存储 + source.execute() |
| ToolManager | `app/tool_manager/manager.py` | ✅ 本地 ToolSet + external 控制 |
| RequirementAgent | `app/agent/requirement_agent.py` | ✅ 需求文档生成并保存 |

## 已跑通链路

```
main.py
  ├── 创建 Project
  ├── 注册 requirement ToolSet
  ├── 创建 RequirementAgent
  └── agent.run(task)
       ├── ToolManager.list_tools("requirement")
       ├── LLMClient.invoke(messages, tools)
       ├── LLM 请求 save_requirement
       ├── ToolManager.call(...)
       └── 写入 projects/Mega_Shit/requirement.md
```

## 下一阶段

| 阶段 | 目标 |
|---|---|
| RequirementWorkflow | 草稿 → 用户确认 → 保存 |
| Memory | 保存跨轮上下文 |
| Shell 逃生舱 | Workflow 人工确认后调用 `Runtime.run_checked()` |
| External MCP | 实现 `ExternalDynamicSource` connector |
| TaskWorkflow | 需求拆任务，生成 `task.md` |
| CodeWorkflow | 代码生成和写入 |

## Shell 设计原则

Shell 不作为普通 Tool 自动暴露。只有 Workflow 获得用户确认后，才能通过 Runtime 执行：

```
Agent 返回需要命令
  ↓
Workflow 请求用户确认
  ↓
Runtime.run_checked(command, cwd, allowed_commands)
```

## 技术演进

```
MVP → CLI → FastAPI → Web Dashboard → Multi-Agent → Remote Runtime → Docker Runtime → Cloud
```

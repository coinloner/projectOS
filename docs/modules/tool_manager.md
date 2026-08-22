# ToolGateway 模块

## 概述

`app.tool_manager` 是 ProjectOS 的工具治理入口。它不再自己实现工具调用循环，而是根据 Catalog 与访问策略将当前已授权的工具包装为 CrewAI `BaseTool`，交给 CrewAI Agent 执行。

工具来源可以是：

- **本地 ToolSet**：项目内已知工具，按 ToolSet 单位注册，默认暴露。
- **MCP Source**：外部动态工具来源，默认按 source 锁住。

本地不做动态扫描。工具暴露半径由 ToolSet 的设计质量控制。

## 架构定位

```
ProjectOS Agent Adapter
  │
  ▼
ToolGateway
  ├── ToolCatalog       # 声明、注册记录、动态发现
  ├── ToolAccessPolicy  # visibility / source grant
  └── ProjectOSTool     # ToolRegistration -> CrewAI BaseTool
       └── ToolSource   # 本地 ToolSet 或 MCP
            ▲
            │
        CrewAI Agent     # 参数校验、工具调用和循环
```

## 核心对象

| 对象 | 职责 |
|---|---|
| `ToolDef` | 描述工具：name / description / parameters，及可选 `execution_modes` |
| `ToolSetSource` | 承载本地 ToolSet，保存 `ToolDef -> Callable` 映射 |
| `ExecutionToolSetSource` | 承载需要可信执行身份的本地工具，系统注入 `ExecutionContext` |
| 动态 `ToolSource` | 未来通过可注入的 MCP client 发现和调用远端工具 |
| `ToolCatalog` | 保存工具声明与其注册上下文 |
| `ToolAccessPolicy` | 判断工具是否可以向 Agent 暴露 |
| `ProjectOSTool` | 持有注册记录，执行时委托对应 `ToolSource` |
| `ToolGateway` | 选择并产出当前 domain 的 CrewAI 工具 |

## 生命周期

| 来源 | 暴露方式 | 存储 | 生命周期 |
|---|---|---|---|
| 本地 ToolSet | `always` | Catalog | session 持久 |
| MCP Source | `on_demand` + `activate_source()` | Catalog 动态刷新 | 单次查询 |

`register_toolset(..., toolset=ToolSetSource(...))` 中的 `toolset` 是本地工具集，不是远端来源；`register_source(..., source=dynamic_source, capability="external_research")` 中的 `source` 才表示 MCP 等实际来源。`capability` 必须是 GraphRunner 与 Agent 约定的稳定标识。动态 Source 已提供真实 MCP connector：`StreamableHttpMCPClient` 通过 MCP Streamable HTTP 协议发现并调用远端工具（内置演示服务 `app/demo/docs_mcp_server.py` 暴露 `search_docs`/`get_doc`，URL 由 `PROJECTOS_DOCS_MCP_URL` 环境变量配置）；批准接口只会激活已注册 source，不会凭空创建 connector。

## CrewAI 边界

`ToolGateway.tools_for(domain, context=...)` 的输出是 `list[BaseTool]`。每个适配工具在 CrewAI 完成参数校验后，直接调用原始 `ToolSource.execute(name, arguments, context=...)`：

```text
ToolDef JSON Schema -> Pydantic args_schema -> CrewAI BaseTool.run()
                                              -> ToolSource.execute()
```

`context` 只能由 GraphRunner 创建和绑定，不属于 JSON Schema。普通 `ToolSetSource` 忽略它；`ExecutionToolSetSource` 拒绝缺少它的调用，用于 Sandbox evidence 等必须追溯到 Trace 和 WorkItem 的动作。

当 `ToolDef.execution_modes` 有值时，Gateway 还会把它与可信的
`ExecutionContext.execution_mode` 比对后才生成 `BaseTool`。这不是访问策略的替代品：
`ToolAccessPolicy` 仍控制来源授权；执行模式只把同一 domain 内不同 WorkItem 的最小工具集
区分开。例如架构分区节点看不到候选创建工具，集成节点看不到暂存写入工具。

因此 ProjectOS 不再维护 `list_tools()`、`call()`、参数 JSON 解析或 tool-calling loop。Gateway 会在 `BaseTool` 实际执行时复查 source 授权，避免已撤销的 MCP 工具对象继续调用远端。当前系统不提供宿主机 Shell 入口；生成项目的执行只能经由 Sandbox。

## 设计原则

- 本地工具不扫描，以 ToolSet 为单位注册。
- Agent 不参与 MCP 授权。
- MCP 默认锁住，GraphRunner 的上层需要时按 source 调用 `activate_source()`。
- `ToolDef` 不包含 `fn`，执行逻辑归 `ToolSource.execute()`。
- `find_sources_for_capability()` 只查注册元数据，不会因查询能力缺口而连接 MCP。

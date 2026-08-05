# ProjectOS 系统架构

## 当前架构

```
                          ┌─────────────────────┐
                          │      Planner         │  □ 未来
                          │   跨 Workflow 规划    │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │      Workflow        │  □ 未来
                          │   单流程状态机        │
                          └──────────┬──────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
  ┌───────▼───────┐         ┌───────▼───────┐         ┌───────▼───────┐
  │ Requirement  │         │    Task       │         │    Code       │
  │    Agent     │         │    Agent     │         │    Agent     │
  │     ✅       │         │     □        │         │     □        │
  └───────┬───────┘         └───────────────┘         └───────────────┘
          │
          │  "有什么工具可用？"
          ▼
  ┌───────────────┐
  │ ToolRegistry  │  ✅  注册 / 发现 / 调用
  └───────┬───────┘
          │
          │  list_tools() / call()
          ▼
  ┌───────────────┐     ┌───────────────┐     ┌───────────────┐
  │  Requirement  │     │    Runtime    │     │    LLMClient  │
  │   ToolSet     │     │     ✅        │     │      ✅       │
  │     ✅        │     │  run()        │     │  invoke()     │
  │ save / load   │     │               │     │  build_*()    │
  └───────────────┘     └───────────────┘     └───────────────┘
```

## 已完成的模块

| 层 | 模块 | 文件 | 职责 |
|---|---|---|---|
| **Agent** | `BaseAgent` | `app/agent/base_agent.py` | 智能执行节点基类，tool-calling loop |
| | `RequirementAgent` | `app/agent/requirement_agent.py` | 接收自然语言，生成结构化需求文档 |
| **ToolRegistry** | `ToolRegistry` | `app/tool_registry/registry.py` | 工具注册、发现、调用 |
| **Tool** | `Requirement` | `app/requirement/requirement.py` | requirement.md 文件操作 |
| | `RequirementToolSet` | `app/requirement/requirement_tool.py` | Agent 可调用的工具封装 |
| | `Runtime` | `app/runtime/Runtime.py` | 命令统一执行入口 |
| **LLM** | `LLMClient` | `app/llm/llm_client.py` | 大模型调用入口，invoke() + build_*() |
| | `LLMResponse` | `app/llm/llm_client.py` | invoke() 统一返回值 |
| | `config` | `app/llm/config.py` | 厂商预设，provider 切换 |
| **Project** | `Project` | `app/project/project.py` | 项目创建、加载、删除、扫描 |

## 调用链路（已跑通）

```
main.py
  │
  ├── registry = ToolRegistry()
  ├── registry.register("save_requirement", ...)
  ├── registry.register("load_requirement", ...)
  │
  └── agent = RequirementAgent(registry)
       │
       agent.run("我要一个博客系统")
         │
         ├── registry.list_tools()           → 发现可用工具
         ├── llm.invoke(messages, tools)      → LLMResponse
         │     ├── is_tool_call → True        → LLM 请求调 save_requirement
         │     └── is_tool_call → False       → LLM 返回文本，结束
         ├── llm.build_assistant_message()    → provider 格式
         ├── registry.call("save_requirement") → 执行工具
         └── llm.build_tool_result()          → 结果喂回 LLM
              │
              ▼
         projects/Test-Blog/requirement.md  ✅
```

## 待实现的模块

| 层 | 模块 | 职责 | 依赖 |
|---|---|---|---|
| **Planner** | Planner | 跨 Workflow 规划和动态调整 | Workflow |
| **Workflow** | RequirementWorkflow | 需求流程状态机（草稿→确认→修改→保存） | Agent, Memory |
| | TaskWorkflow | 需求 → 任务拆解流程 | Agent |
| | CodeWorkflow | 任务 → 代码生成流程 | Agent, Runtime |
| **Memory** | Memory | 会话记忆，跨 run() 持久化 | — |
| **Policy** | Policy | Prompt 模板管理 | — |
| **ContextBuilder** | ContextBuilder | Context 拼接策略 | Memory, Policy |
| **Agent** | TaskAgent | 需求 → 任务拆解 | RequirementAgent（参考实现） |
| | CodeAgent | 代码生成 | TaskAgent（参考实现） |
| **Tool** | TaskToolSet | task.md 文件操作 | Requirement（参考实现） |
| | CodeToolSet | 代码文件读写 | Requirement（参考实现） |

## 技术演进

```
MVP（当前）          CLI                FastAPI           Web Dashboard
    │                  │                   │                   │
    ▼                  ▼                   ▼                   ▼
 单项目本地       命令行工具          REST API 服务       可视化管理台
 单 Agent         多项目支持          远程调用            项目管理界面
 DeepSeek        批量处理            用户系统            实时日志
                                                          
    ──────────────────────────────────────────────────────────────►

    Multi-Agent       Remote Runtime      Docker Runtime      Cloud
        │                   │                   │               │
        ▼                   ▼                   ▼               ▼
    多 Agent 编排      远端命令执行        容器化隔离         云端项目
    并行执行           安全沙箱            环境一致性         团队协作
```

## 关键设计决策

| 决策 | 选择 | 原因 |
|---|---|---|
| Tool 注册方式 | 方案 A（启动时集中注册） | 可见性优先，将来可切换 MCP |
| Tool Calls 格式 | 方案 B（简化格式） | provider 无关，换模型 Agent 不动 |
| Agent 管理注册 | 不管理，外部注入 Registry | 注册与使用分离 |
| Provider 格式 | 收敛在 LLMClient.build_*() | Agent 不碰 provider 格式 |
| 返回值类型 | LLMResponse（dataclass） | 不泄漏 SDK，不靠字符串猜测 |

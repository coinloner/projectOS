# LLM 模块

`app.llm` 是 ProjectOS 对 CrewAI 模型配置的窄适配层。它只选择 provider、读取环境变量并构造 CrewAI `LLM`；不执行 Agent、规划、工具调用或业务逻辑。

## 文件职责

| 文件 | 职责 |
|---|---|
| `config.py` | Provider 预设、默认 provider 与环境变量覆盖规则 |
| `factory.py` | 加载 `.env` 并构造 CrewAI `LLM` |

## 选择顺序

```text
build_llm(provider=...)
  -> PROJECTOS_LLM_PROVIDER
  -> ACTIVE_PROVIDER (默认 fhl / gpt-5.6-terra)
```

API Key 只从对应环境变量读取，例如 `DEEPSEEK_API_KEY`；它不会写入项目产物、Planner 上下文或 Docker Sandbox。
Provider 和模型由用户请求选择；`PROJECTOS_LLM_PROVIDER`、`PROJECTOS_LLM_MODEL` 和
`PROJECTOS_LLM_BASE_URL` 仅作为旧部署的兼容默认覆盖，不再是必需配置。

### 使用硅基流动

硅基流动兼容 OpenAI API，可以只通过部署环境切换，不需要修改 Planner、Agent 或
GraphRunner：

```dotenv
SILICONFLOW_API_KEY=sk-...
```

硅基流动在 CrewAI 内部使用 `openai` 兼容适配器；API Key 环境变量是
`SILICONFLOW_API_KEY`。模型列表通过 `/api/v1/llm/providers/siliconflow/models` 动态获取，
不会把某个模型写死为唯一选择。

模型切换属于用户请求配置，不需要修改代码或 `.env`。

当前配置由 Planner 和所有 Domain Agent 共享。模型切换属于部署配置，不属于 Workflow 或 Agent 规划决策。

`build_llm()` 对所有 Provider 默认开启 `stream=True`（可用
`PROJECTOS_LLM_STREAM=false` 显式关闭）。中转端出现“返回部分 SSE chunk 但不发送终态”时，
不得按 Provider 静默降级为非流式；应由 CrewAI 的
事件总线由编排进度监听器消费，记录 `llm_call_started`、`llm_stream_chunk`、完成和失败，
只写 chunk 数、字节数和时间戳，不写入 token 正文。Provider 不支持 SSE 时仍可正常完成请求，
但进度阶段会显示为 `llm_request`，由 Worker 的硬截止和空闲阈值兜底。

对于 Responses provider-hosted tools，ProjectOS 默认拒绝 `builtin_tools`，以确保工具调用
不会绕过 `ToolGateway` 的 domain、grant、execution mode 和 retry allowlist 控制。未来扩展
设计见[Provider 官方工具扩展项](../roadmap/provider-tools.md)。

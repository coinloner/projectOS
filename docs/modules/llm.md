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
  -> ACTIVE_PROVIDER (默认 deepseek)
```

`PROJECTOS_LLM_MODEL` 与 `PROJECTOS_LLM_BASE_URL` 可以覆盖已选 provider 的预设。API Key 只从对应环境变量读取，例如 `DEEPSEEK_API_KEY`；它不会写入项目产物、Planner 上下文或 Docker Sandbox。

当前配置由 Planner 和所有 Domain Agent 共享。模型切换属于部署配置，不属于 Workflow 或 Agent 规划决策。

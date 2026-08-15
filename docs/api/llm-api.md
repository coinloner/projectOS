# LLM API 接口文档

## Provider 配置

```python
get_provider_config(provider: str | None = None) -> dict[str, str]
```

选择优先级为显式 `provider`、`PROJECTOS_LLM_PROVIDER`、`ACTIVE_PROVIDER`。返回 CrewAI 所需的 `model`、`base_url`、`api_key_env` 与 `crewai_provider`；未知 provider 会抛出 `RuntimeError`。

环境变量 `PROJECTOS_LLM_MODEL` 和 `PROJECTOS_LLM_BASE_URL` 会覆盖 provider 预设。

## LLM 工厂

```python
build_llm(provider: str | None = None) -> crewai.LLM
```

工厂加载项目根目录 `.env`，读取当前 provider 对应的 API Key，并创建 CrewAI `LLM`，但不会在构造阶段发送模型请求。缺少 API Key 时抛出 `RuntimeError`；密钥不得被记录到文档、artifact、日志或 Sandbox。

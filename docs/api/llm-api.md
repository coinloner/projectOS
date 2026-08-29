# LLM API 接口文档

## Provider 配置

```python
get_provider_config(provider: str | None = None) -> dict[str, str]
```

选择优先级为显式 `provider`、`PROJECTOS_LLM_PROVIDER`、`ACTIVE_PROVIDER`。当前代码默认是
`siliconflow` / `deepseek-ai/DeepSeek-V4-Pro`。返回 CrewAI 所需的 `model`、`base_url`、`api_key_env` 与
`crewai_provider`；未知 provider 会抛出 `RuntimeError`。

用户请求中的 provider/model/base_url 优先于部署默认值。环境变量 `PROJECTOS_LLM_PROVIDER`、
`PROJECTOS_LLM_MODEL` 和 `PROJECTOS_LLM_BASE_URL` 仅作为向后兼容的默认覆盖；`.env` 推荐只保存
各平台 API key 和流式开关。

因此切换模型不需要修改 Python 代码或 `.env`；前端从模型目录接口读取列表后，把选择放入本次请求。

内置 provider 包括 `deepseek`、`openai`、`claude`、`siliconflow` 和 `fhl`。硅基流动与 FHL 使用
OpenAI 兼容协议，默认地址为 `https://api.siliconflow.cn/v1`，密钥从
`SILICONFLOW_API_KEY` 读取；模型名由请求选择，provider 默认模型仅作为兼容回退。
FHL 中转站地址为 `https://www.fhl.mom/v1`，密钥从 `FHL_API_KEY` 读取，默认模型为
`gpt-5.6-terra`。

HTTP 控制面提供 `GET /api/v1/llm/providers` 供前端展示可选项。创建受控运行或发送会话消息时，
在请求体中传入 `provider`、`model`、`base_url` 即可只为本轮选择模型；选择会持久化到 Trace，
恢复和修复不会回退到环境默认值。

对支持 OpenAI 兼容协议的平台，前端可调用
`GET /api/v1/llm/providers/{provider}/models` 动态读取模型列表；硅基流动模型切换不需要修改
代码或 `.env`。

Pro 用户还可以传入 `llm_overrides`：

```json
{
  "provider": "siliconflow",
  "model": "deepseek-ai/DeepSeek-V4-Pro",
  "llm_overrides": {
    "review_agent": {
      "provider": "openai",
      "model": "gpt-4.1"
    }
  }
}
```

未配置覆盖的 Agent 使用默认选择；覆盖只对当前 Trace 生效，且不会把 API key 写入 Trace。

## LLM 工厂

```python
build_llm(
    provider: str | None = None,
    *,
    selection: LLMSelection | None = None,
) -> crewai.LLM
```

工厂加载项目根目录 `.env`，读取当前 provider 对应的 API Key，并创建 CrewAI `LLM`，但不会在构造阶段发送模型请求。缺少 API Key 时抛出 `RuntimeError`；密钥不得被记录到文档、artifact、日志或 Sandbox。

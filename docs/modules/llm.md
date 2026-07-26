# LLMClient 模块

## 概述

`app.llm.llm_client.LLMClient` 是 ProjectOS 的统一大模型调用入口。**所有模块的 LLM 调用必须经由 `LLMClient.invoke()`**，不得直接使用各 SDK。通过 `config.py` 中的厂商预设实现一键切换 DeepSeek / OpenAI / Claude，无需修改业务代码。

## 依赖

| 模块 | 用途 |
|---|---|
| `openai` | DeepSeek / OpenAI 兼容 SDK |
| `python-dotenv` | 从 `.env` 加载 API Key |
| `app.llm.config` | 厂商预设与激活厂商配置 |

## 类设计

```
LLMClient
├── __init__(provider?)    # 初始化客户端，读取 API Key 和厂商配置
└── invoke(prompt) -> str  # 实例方法：发送提示词，返回纯文本
```

## 配置层

```
config.py
├── _PROVIDERS              # 厂商预设字典（deepseek / openai / claude）
├── ACTIVE_PROVIDER         # 当前激活厂商，默认 "deepseek"
└── get_provider_config()   # 根据厂商名返回 {base_url, model, api_key_env}
```

## 方法签名

### `__init__(provider: Optional[str] = None)`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | `str \| None` | 否 | 厂商名（`"deepseek"` / `"openai"` / `"claude"`），不传则使用 `ACTIVE_PROVIDER` |

- 从 `os.environ` 读取对应厂商的 API Key
- Key 缺失 → `RuntimeError`
- 未知 provider → `RuntimeError`

### `invoke(prompt: str) -> str`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `str` | 是 | 用户提示词，不能为空 |

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | prompt 为空 |
| `RuntimeError` | API 调用失败（网络异常、鉴权失败等） |
| `RuntimeError` | LLM 返回空响应或无 choices |
| `RuntimeError` | LLM 返回 content 为 None |

## 切换厂商

```python
# 全局默认：修改 config.py
ACTIVE_PROVIDER = "claude"

# 单次调用：构造时传入
client = LLMClient(provider="openai")
```

## 添加新厂商

在 `config.py` 的 `_PROVIDERS` 字典中增加一条即可，无需修改 `llm_client.py`：

```python
"gemini": {
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "model": "gemini-2.5-pro",
    "api_key_env": "GEMINI_API_KEY",
},
```

## 设计原则

- **不泄漏 SDK 类型**：`invoke()` 只返回 `str`，不返回 `ChatCompletion`、`Choice`、`Message`
- **不设计 Provider 抽象**：通过字典配置切换，无需 Factory / 继承
- **异常统一**：所有错误统一为 `RuntimeError`，与 `Runtime` 模块风格一致

# LLMClient 模块

## 概述

`app.llm.llm_client.LLMClient` 是 ProjectOS 的统一大模型调用入口。**所有模块的 LLM 调用必须经由 `LLMClient.invoke()`**，不得直接使用各 SDK。通过 `config.py` 中的厂商预设实现一键切换 DeepSeek / OpenAI / Claude，无需修改业务代码。

## 依赖

| 模块 | 用途 |
|---|---|
| `openai` | DeepSeek / OpenAI 兼容 SDK |
| `python-dotenv` | 从 `.env` 加载 API Key |
| `app.llm.config` | 厂商预设与激活厂商配置 |

## 数据结构

### `LLMResponse`

```python
@dataclass
class LLMResponse:
    content: Optional[str]       # 文本回复，tool_calls 时为 None
    tool_calls: Optional[list]   # 简化格式，文本回复时为 None

    @property
    def is_tool_call(self) -> bool   # tool_calls 非空时为 True
    def __str__(self) -> str         # 返回 content 或 ""
```

`tool_calls` 使用 provider 无关的简化格式（方案 B）：

```python
[
    {"id": "call_xxx", "name": "save_requirement", "arguments": '{"content": "..."}'},
]
```

## 类设计

```
LLMClient
├── __init__(provider?)                          # 初始化客户端
├── invoke(prompt, tools?) -> LLMResponse         # 发送请求
├── build_assistant_message(tool_calls) -> dict   # 简化格式 → provider 格式
└── build_tool_result(call_id, result) -> dict    # 构建 tool 结果消息
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

### `invoke(prompt: Union[str, list], tools: Optional[list] = None) -> LLMResponse`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `str \| list` | 是 | 字符串（Simple 模式）或 messages 列表（Agent 模式） |
| `tools` | `list \| None` | 否 | OpenAI 格式的工具定义列表 |

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | prompt 为空 |
| `RuntimeError` | API 调用失败（网络异常、鉴权失败等） |
| `RuntimeError` | LLM 返回空响应或无 choices |
| `RuntimeError` | LLM 返回 content 为 None |

**返回值** — `LLMResponse`：

| 场景 | `response.content` | `response.tool_calls` | `response.is_tool_call` |
|---|---|---|---|
| LLM 文本回复 | `"这是回复内容"` | `None` | `False` |
| LLM 请求调工具 | `None` | `[{id, name, arguments}]` | `True` |

### `build_assistant_message(tool_calls: list[dict]) -> dict`

将简化的 `tool_calls` 转为 provider 格式的 assistant 消息。当前实现适配 OpenAI / DeepSeek，将来切换 Anthropic 时在此方法内分支。

```python
# 输入（简化格式）
[{"id": "call_1", "name": "save", "arguments": "{...}"}]

# 输出（OpenAI 格式）
{
    "role": "assistant",
    "content": None,
    "tool_calls": [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "save", "arguments": "{...}"},
    }],
}
```

### `build_tool_result(call_id: str, result: str) -> dict`

构建 provider 格式的 tool 结果消息：

```python
{"role": "tool", "tool_call_id": "call_1", "content": "✅ 需求文档已保存"}
```

## 切换厂商

```python
# 全局默认：修改 config.py
ACTIVE_PROVIDER = "claude"

# 单次调用：构造时传入
client = LLMClient(provider="openai")
```

## 添加新厂商

在 `config.py` 的 `_PROVIDERS` 字典中增加一条即可：

```python
"gemini": {
    "base_url": "https://generativelanguage.googleapis.com/v1beta",
    "model": "gemini-2.5-pro",
    "api_key_env": "GEMINI_API_KEY",
},
```

如新厂商的消息格式不同，同步修改 `build_assistant_message()` 和 `build_tool_result()`。

## 设计原则

- **不泄漏 SDK 类型**：`invoke()` 返回 `LLMResponse`（纯数据），不返回 SDK 对象
- **Provider 格式封装**：消息格式差异收敛在 `build_*()` 方法内，Agent 无感知
- **Tool Calls 简化格式**：provider 无关，换模型时 Agent 不动
- **异常统一**：所有错误统一为 `RuntimeError`，与 `Runtime` 模块风格一致

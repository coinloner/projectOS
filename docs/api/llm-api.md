# LLMClient API 接口文档

> **定位**：定义 `LLMClient` 模块对外的公共契约。所有实现变更不得破坏此文档中声明的签名、返回值结构和异常语义。

## 1. LLMResponse 数据结构

```python
@dataclass
class LLMResponse:
    content: Optional[str] = None      # 文本回复
    tool_calls: Optional[list] = None  # 工具调用请求（简化格式）

    @property
    def is_tool_call(self) -> bool     # tool_calls 非空 → True
    def __str__(self) -> str           # 返回 content 或 ""
```

### tool_calls 格式（方案 B）

```python
[
    {
        "id": "call_xxxxxxxxxxxxx",
        "name": "save_requirement",
        "arguments": '{"content": "# 需求文档..."}',
    }
]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `str` | 工具调用唯一标识 |
| `name` | `str` | 工具名称 |
| `arguments` | `str` | JSON 字符串，工具参数 |

---

## 2. 初始化

```python
LLMClient(provider: Optional[str] = None) -> LLMClient
```

| 参数 | 类型 | 默认值 | 必填 |
|---|---|---|---|
| `provider` | `str \| None` | `None`（使用 `ACTIVE_PROVIDER`） | 否 |

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | 对应厂商的 API Key 环境变量未设置 |
| `RuntimeError` | `provider` 不在 `_PROVIDERS` 中 |

---

## 3. 调用 LLM

```python
LLMClient.invoke(
    prompt: Union[str, list],
    tools: Optional[list] = None,
) -> LLMResponse
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `str \| list` | 是 | 字符串（Simple）或 messages 列表（Agent） |
| `tools` | `list \| None` | 否 | OpenAI 格式的工具定义列表 |

### 返回值判断

```python
response = client.invoke(...)

if response.tool_calls is None:
    text = response.content       # LLM 文本回复
else:
    for tc in response.tool_calls:
        tc["name"]                # 工具名
        tc["arguments"]           # 参数 JSON
```

### 异常

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | `prompt` 为空或全空白字符 |
| `RuntimeError` | API 调用失败（含网络异常、鉴权失败、超时等） |
| `RuntimeError` | LLM 返回空 `choices` 列表 |
| `RuntimeError` | `message.content` 为 `None` 且无 `tool_calls` |

---

## 4. 消息构建

### `build_assistant_message`

```python
LLMClient.build_assistant_message(tool_calls: list[dict]) -> dict
```

将简化 tool_calls 转为 provider 特定的 assistant 消息。Agent 调用此方法拼消息，不直接构造 provider 格式。

### `build_tool_result`

```python
LLMClient.build_tool_result(call_id: str, result: str) -> dict
```

构建 provider 特定的 tool 结果消息。

---

## 5. 配置切换

### 厂商预设

| 厂商 | base_url | model | api_key_env |
|---|---|---|---|
| `deepseek` | `https://api.deepseek.com` | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| `openai` | `https://api.openai.com/v1` | `gpt-4.1` | `OPENAI_API_KEY` |
| `claude` | `https://api.anthropic.com` | `claude-sonnet-5-20250901` | `ANTHROPIC_API_KEY` |

### 切换方式

```python
# 构造时传入 —— 不影响全局默认
client = LLMClient(provider="openai")

# 修改 config.py —— 改变全局默认
ACTIVE_PROVIDER = "claude"
```

---

## 兼容性约定

1. **`invoke()` 返回 `LLMResponse`** —— 不得返回 SDK 类型（`ChatCompletion`、`Choice`、`Message`）
2. **`tool_calls` 使用简化格式** —— provider 无关，切换模型时格式不变
3. **Provider 格式收敛在 `build_*()`** —— Agent 不直接构造 provider 消息格式
4. **异常统一为 RuntimeError** —— 与 `Runtime` 模块保持一致
5. **API Key 统一从环境变量读取** —— 不通过构造函数传入 key
6. **新增厂商不改变公共接口** —— 追加 `_PROVIDERS` 条目 + 适配 `build_*()` 分支即可

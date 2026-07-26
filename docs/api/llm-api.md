# LLMClient API 接口文档

> **定位**：定义 `LLMClient` 模块对外的公共契约。所有实现变更不得破坏此文档中声明的签名、返回值结构和异常语义。

## 1. 初始化

```python
LLMClient(provider: Optional[str] = None) -> LLMClient
```

| 参数 | 类型 | 默认值 | 必填 |
|---|---|---|---|
| `provider` | `str \| None` | `None`（使用 `ACTIVE_PROVIDER`） | 否 |

- 从对应环境变量读取 API Key
- 初始化 OpenAI 兼容客户端
- 设置 `base_url` 和 `model`

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | 对应厂商的 API Key 环境变量未设置 |
| `RuntimeError` | `provider` 不在 `_PROVIDERS` 中 |

---

## 2. 调用 LLM

```python
LLMClient.invoke(prompt: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | `str` | 是 | 用户提示词 |

### 返回值

`str` —— LLM 回复的纯文本内容

### 异常

| 异常 | 触发条件 |
|---|---|
| `RuntimeError` | `prompt` 为空或全空白字符 |
| `RuntimeError` | API 调用失败（含网络异常、鉴权失败、超时等） |
| `RuntimeError` | LLM 返回空 `choices` 列表 |
| `RuntimeError` | `message.content` 为 `None` |

---

## 3. 配置切换

```python
# config.py —— 全局默认厂商
ACTIVE_PROVIDER: str = "deepseek"
```

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

1. **签名不可变** —— `invoke(prompt: str) -> str` 是对外唯一的调用契约，新增参数只能以可选方式追加。
2. **不返回 SDK 类型** —— `invoke()` 的返回值绝不会是 `ChatCompletion`、`Choice`、`Message` 等 SDK 类型，调用方无需了解底层 SDK。
3. **异常统一为 RuntimeError** —— 与 `Runtime` 模块保持一致，调用方只需捕获 `RuntimeError`。
4. **API Key 统一从环境变量读取** —— 不通过构造函数传入 key，避免 key 散落在代码中。
5. **新增厂商不改变接口** —— 在 `_PROVIDERS` 中追加厂商不会改变 `LLMClient` 的公共签名。
6. **单次调用，不维护对话历史** —— `invoke()` 每次调用都是独立的，不记录历史。对话管理是上层 Workflow 的职责。

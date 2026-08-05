# ToolRegistry API 接口文档

> **定位**：定义 `ToolRegistry` 模块对外的公共契约。Agent 通过此接口发现和调用工具。

## 1. 注册工具

```python
ToolRegistry.register(
    name: str,
    fn: Callable,
    description: str,
    parameters: dict,
) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称，LLM 可见 |
| `fn` | `Callable` | 是 | 工具实现函数 |
| `description` | `str` | 是 | 工具用途，LLM 据此判断何时调用 |
| `parameters` | `dict` | 是 | JSON Schema 格式的参数定义 |

- 不抛异常
- 同名工具后注册的覆盖先注册的

---

## 2. 列出工具

```python
ToolRegistry.list_tools() -> Optional[list[dict]]
```

返回 OpenAI 格式的工具定义列表：

```python
[
    {
        "type": "function",
        "function": {
            "name": "save_requirement",
            "description": "保存需求文档",
            "parameters": {...},
        },
    },
]
```

- 无注册工具时返回 `None`
- Agent 将返回值直接传给 `LLMClient.invoke(tools=...)`

---

## 3. 调用工具

```python
ToolRegistry.call(name: str, arguments: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称 |
| `arguments` | `str` | 是 | JSON 格式的工具参数 |

### 返回值

始终返回 `str`，不抛异常：

| 场景 | 返回值 |
|---|---|
| 执行成功 | 工具返回的结果字符串 |
| 工具未注册 | `"Error: 未知工具 'xxx'"` |
| 执行异常 | `"Error: 工具 'xxx' 执行失败: {异常信息}"` |

---

## 兼容性约定

1. **`register()` 由启动入口调用** —— Agent 内部不调用此方法
2. **`list_tools()` 返回 OpenAI 格式** —— 与 `LLMClient.invoke()` 的 `tools` 参数直接兼容
3. **`call()` 不抛异常** —— 错误以字符串返回，由 LLM 自行处理
4. **接口不随注册方式改变** —— 将来从人工注册切换到 MCP 动态发现时，`list_tools()` 和 `call()` 签名不变
5. **参数由注册方定义** —— `parameters` 的 JSON Schema 由 Tool 模块提供，Registry 不做校验

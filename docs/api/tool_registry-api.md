# ToolRegistry API 接口文档

> **定位**：定义 `ToolRegistry` 模块对外的公共契约。ToolManager 通过此接口存储和执行工具。

## 1. 注册工具

```python
ToolRegistry.register(
    name: str,
    tool_def: dict,
    source: ToolSource,
) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称，LLM 可见 |
| `tool_def` | `dict` | 是 | OpenAI 格式的工具定义 |
| `source` | `ToolSource` | 是 | 提供该工具的 ToolSource（执行时委托回去） |

- 不抛异常
- 同名工具后注册的覆盖先注册的
- 不再存储 `fn` 字段 —— 执行逻辑在 `source.execute()` 中

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

---

## 3. 调用工具

```python
ToolRegistry.call(name: str, arguments: str) -> str
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称 |
| `arguments` | `str` | 是 | JSON 格式的工具参数 |

### 执行路径

委托给 `source.execute(name, parsed_args)` —— 本地函数调用和 MCP RPC 在这里分叉。

### 返回值

始终返回 `str`，不抛异常：

| 场景 | 返回值 |
|---|---|
| 执行成功 | 工具返回的结果字符串 |
| 工具未注册 | `"Error: 未知工具 'xxx'"` |
| 执行异常 | `"Error: 工具 'xxx' 执行失败: {异常信息}"` |

---

## 兼容性约定

1. **`register()` 由 ToolManager 调用** —— 外部不直接调用此方法
2. **`list_tools()` 返回 OpenAI 格式** —— 与 `LLMClient.invoke()` 的 `tools` 参数直接兼容
3. **`call()` 不抛异常** —— 错误以字符串返回，由 LLM 自行处理
4. **接口不随注册方式改变** —— 将来从人工注册切换到 MCP 动态发现时，签名不变
5. **执行委托 source** —— `call()` 不直接调函数，兼容 MCP 工具

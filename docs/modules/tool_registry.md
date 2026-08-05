# ToolRegistry 模块

## 概述

`app.tool_registry.registry.ToolRegistry` 是 ProjectOS 的工具注册中心。Agent 通过它发现可用工具和执行工具调用。当前阶段采用人工注册（方案 A：启动时集中注册），将来切换 MCP 动态发现时只改内部实现，接口不变。

## 架构定位

```
Agent → ToolRegistry → Tool
         ├── register()    # 人工注册（当前）
         ├── list_tools()  # 发现 → Agent 问"有什么工具？"
         └── call()        # 执行 → Agent 说"调这个"
```

## 依赖

| 模块 | 用途 |
|---|---|
| `json` | 解析工具参数 JSON |
| `typing` | 类型标注 |

## 类设计

```
ToolRegistry
├── register(name, fn, description, parameters)  # 注册工具
├── list_tools() -> Optional[list[dict]]         # 返回工具定义列表
└── call(name, arguments) -> str                 # 执行工具
```

## 方法签名

### `register(name: str, fn: Callable, description: str, parameters: dict) -> None`

注册一个工具。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称（LLM 可见） |
| `fn` | `Callable` | 是 | 工具实现函数 |
| `description` | `str` | 是 | 工具用途描述（LLM 据此判断何时调用） |
| `parameters` | `dict` | 是 | JSON Schema 格式的参数定义 |

工具定义存储格式（OpenAI 兼容）：

```python
{
    "type": "function",
    "function": {
        "name": "save_requirement",
        "description": "保存需求文档到项目目录",
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...],
        },
    },
}
```

### `list_tools() -> Optional[list[dict]]`

返回所有已注册工具的 OpenAI 格式定义列表。无注册工具时返回 `None`。

Agent 通过此方法询问"有什么工具可用？"，然后将结果传给 `LLMClient.invoke(tools=...)`。

### `call(name: str, arguments: str) -> str`

执行已注册的工具。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | 是 | 工具名称 |
| `arguments` | `str` | 是 | JSON 格式的工具参数 |

| 返回值 | 说明 |
|---|---|
| `str` | 工具执行结果（成功时） |
| `"Error: 未知工具 'xxx'"` | 工具未注册 |
| `"Error: 工具 'xxx' 执行失败: ..."` | 工具执行异常 |

---

## 使用示例

```python
from app.tool_registry.registry import ToolRegistry

# 启动时集中注册
registry = ToolRegistry()
registry.register(
    name="save_requirement",
    fn=lambda content: ...,
    description="保存需求文档",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Markdown 内容"},
        },
        "required": ["content"],
    },
)
registry.register(
    name="load_requirement",
    fn=lambda: ...,
    description="读取需求文档",
    parameters={"type": "object", "properties": {}},
)

# Agent 使用
tools = registry.list_tools()       # → [def1, def2]
result = registry.call("save_requirement", '{"content": "# 文档"}')
```

---

## 设计原则

- **注册与使用分离**：`register()` 在启动时调用，`list_tools()` / `call()` 在运行时由 Agent 调用
- **Agent 不管理注册**：`ToolRegistry` 是独立层，Agent 只接收已注册好的实例
- **接口稳定**：`list_tools()` 和 `call()` 的签名不随注册方式改变（人工 → MCP）
- **错误不抛异常**：`call()` 对未知工具和执行错误返回 Error 字符串，不抛异常，让 LLM 自行决定如何处理

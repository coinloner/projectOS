# ToolRegistry 模块

## 概述

`app.tool_registry.registry.ToolRegistry` 是工具的持久存储层。它不决定工具从哪里来、不决定工具暴露给谁，只保存 ToolManager 已经允许加载的本地 ToolSet 工具。

## 职责

```
ToolManager
  └── registry.register(name, tool_def, source)
        ↓
ToolRegistry
  ├── list_tools()       # 返回 OpenAI tools 格式
  └── call(name, args)   # 委托 source.execute()
```

## 设计重点

- Registry 不保存 `fn`
- Registry 保存 `source`
- 执行时委托 `source.execute(name, arguments)`

这样本地 Python 函数和未来 MCP 工具可以共享同一条调用链。

## 方法

### register

```python
register(name: str, tool_def: dict, source: ToolSource) -> None
```

将工具定义和对应来源存入 Registry。同名工具后注册覆盖先注册。

### list_tools

```python
list_tools() -> Optional[list[dict]]
```

返回 OpenAI tools 格式的工具定义列表。无工具时返回 `None`。

### call

```python
call(name: str, arguments: str) -> str
```

解析 JSON 参数，找到对应 source，并调用：

```python
source.execute(name, args)
```

错误以字符串返回，不向上抛出。

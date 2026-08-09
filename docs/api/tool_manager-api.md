# ToolManager API 接口文档

## 1. 注册本地 ToolSet

```python
ToolManager.register_toolset(
    domain: str,
    name: str,
    source: ToolSource,
) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `domain` | `str` | 是 | 工具域，如 `"requirement"` |
| `name` | `str` | 是 | ToolSet 名称，如 `"base"` |
| `source` | `ToolSource` | 是 | 通常为 `ToolSetSource` |

注册后不会立即 discover。首次 `list_tools(domain)` 时懒加载。

## 2. 注册 External Source

```python
ToolManager.register_source(
    domain: str,
    name: str,
    source: ToolSource,
) -> None
```

用于 MCP 等外部动态来源。注册不代表暴露，必须先启用和激活。

## 3. External 控制

```python
ToolManager.enable_external(domain: str) -> None
ToolManager.activate_external(domain: str) -> None
ToolManager.deactivate_external(domain: str) -> None
```

| 方法 | 说明 |
|---|---|
| `enable_external()` | 授权某个 domain 可以使用 external |
| `activate_external()` | 激活 external discover，未 enable 时抛 `PermissionError` |
| `deactivate_external()` | 关闭 external 并清理临时缓存 |

## 4. 列出工具

```python
ToolManager.list_tools(domain: str) -> Optional[list[dict]]
```

返回当前 domain 可用的 OpenAI tools 格式列表：

1. 本地 ToolSet（持久缓存）
2. 已激活的 External Source（单次 discover）

## 5. 调用工具

```python
ToolManager.call(name: str, arguments: str, domain: str) -> str
```

查找顺序：

1. external 临时缓存
2. ToolRegistry 中的本地 ToolSet

始终返回 `str`。错误也以字符串返回。

## 兼容性约定

1. Agent 只依赖 `list_tools(domain)` 和 `call(name, arguments, domain)`。
2. Agent 不控制 external。
3. 本地工具不动态扫描。
4. External 工具不持久化。
5. 新 Source 只需实现 `discover()` 和 `execute()`。

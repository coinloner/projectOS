# ToolGateway API 接口文档

## 1. 注册本地 ToolSet

```python
ToolGateway.register_toolset(
    domain: str,
    name: str,
    toolset: ToolSource,
    *,
    capability: str | None = None,
    exposure: ToolExposure = ToolExposure.ALWAYS,
) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `domain` | `str` | 是 | 工具域，如 `"requirement"` |
| `name` | `str` | 是 | ToolSet 名称，如 `"base"` |
| `toolset` | `ToolSource` | 是 | 通常为 `ToolSetSource`；承载本地 `ToolDef -> Python 方法` 映射 |

注册时立即把本地 `ToolDef` 写入 Catalog。默认 `always`，所以 Agent 可见。

## 2. 注册 MCP 或按需 Source

```python
ToolGateway.register_source(
    domain: str,
    name: str,
    source: ToolSource,
    *,
    capability: str | None = None,
    exposure: ToolExposure = ToolExposure.ON_DEMAND,
) -> None
```

用于 MCP 等动态来源。`capability` 是 GraphRunner 用于匹配能力缺口的稳定标识，例如 `external_research`。注册不代表发现或暴露；默认在 source 激活后才会 discover。

## 3. Source 授权

```python
ToolGateway.activate_source(domain: str, name: str) -> None
ToolGateway.deactivate_source(domain: str, name: str) -> None
```

| 方法 | 说明 |
|---|---|
| `activate_source()` | 授权指定 source，并允许动态来源 discover |
| `deactivate_source()` | 撤销指定 source 的会话授权 |

旧版 `enable_external()`、`activate_external()`、`deactivate_external()` 暂时保留为兼容入口；新的 GraphRunner 上层必须使用按 source 授权的 API。

## 4. 获取 CrewAI 工具

```python
ToolGateway.tools_for(domain: str) -> list[BaseTool]
```

返回当前 domain 可用的 CrewAI `BaseTool` 列表：

1. `always` 的本地 ToolSet
2. 已激活 source 中、策略允许的动态工具

CrewAI 负责调用工具、参数验证和调用循环。每个 `BaseTool` 会把验证后的参数委托给注册时对应的 `ToolSource.execute()`。

## 5. 按能力查找来源

```python
ToolGateway.find_sources_for_capability(
    domain: str,
    capability: str,
) -> list[SourceRegistration]
```

为 GraphRunner 查找可满足能力缺口的动态来源，例如将 `external_research` 解析为 `docs-mcp`。该查询不连接 MCP、不 discover 工具、更不会自动授权来源。

## 兼容性约定

1. Agent 只依赖 `tools_for(domain)`，不直接依赖 Catalog 或 Policy。
2. CrewAI 负责工具调用与参数验证；Gateway 不提供 `call()`。
3. Agent 不控制 source 授权。
4. 本地工具不动态扫描。
5. 未激活的动态来源不会 discover。
6. 新 Source 只需实现 `discover()` 和 `execute()`。

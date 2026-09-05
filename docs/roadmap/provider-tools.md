# Provider 官方工具扩展项

## 当前决策

ProjectOS 默认禁止 Responses provider-hosted tools，包括 `web_search`、`file_search`、
`code_interpreter`、`computer_use` 以及未经 `ToolGateway` 授权的原生 MCP 工具。

当前 Worker Agent 只能使用由 `ToolGateway.tools_for(domain, context)` 返回、并由
`ProjectOSTool` 包装的 function tools。Planner 保持 `tools=[]`。

默认策略由以下环境变量控制：

```dotenv
PROJECTOS_ALLOW_PROVIDER_BUILTINS=false
```

只有在明确评估并批准后，才允许设置为 `true`。这个开关是部署级实验开关，不是普通
Agent 可以自行改变的请求参数。

## 为什么默认关闭

官方工具可能在协议和执行性能上优于 ProjectOS 自建替代方案，但直接启用会引入一条
不经过 ProjectOS 控制面的执行路径，可能绕过 domain、grant、execution mode、retry
allowlist、Trace/Memory 审计，以及项目级成本和数据驻留策略。中转站也可能支持文本
Responses，却不完整转发官方工具事件。

## 未来开放前的必要设计

### 1. 统一工具来源分类

每个工具需要明确标记来源：

```text
projectos_function
gateway_mcp
provider_builtin
direct_remote
unknown
```

`unknown` 必须拒绝。`provider_builtin` 必须有明确的能力定义、作用域、数据处理说明
和审计适配器。

### 2. 建立 BuiltinToolGateway

未来若开放官方工具，不应把 `builtin_tools` 直接塞进普通 Agent。建议增加独立的
`BuiltinToolGateway`，负责工具能力注册和版本固定、project/trace/node 作用域、预算与
并发限制、Trace 事件、结果摘要与敏感信息过滤、失败/重试/人工审批，以及与现有
`ToolAccessPolicy` 的统一决策接口。

### 3. 必须保持的不变量

```text
未获授权的 builtin 不出现在请求 tools 中
已撤销授权的 builtin 不能继续执行
node 级授权不能泄漏到 sibling node
retry allowlist 能进一步收窄工具集合
工具调用和结果都有可关联的 trace_id / work_item_id / attempt
```

### 4. Responses 事件兼容性

真实 provider 和中转站必须验证 `response.output_item.added`、
`response.function_call_arguments.delta`、`response.output_item.done`、
`response.completed`、工具结果提交后的下一轮响应，以及断流、空流和重复终态处理。
关键终态缺失时，应停止节点并记录 transport/provider 失败，不得静默降级。

### 5. 性能与成本评估

至少对比首 token 延迟、工具调用总延迟、SSE 事件数量和丢包率、token/tool-call 成本、
重试率、节点完成率，以及与 ProjectOS 自建工具的实际收益。

## 建议的开放顺序

1. 只读、低风险工具（例如受限搜索）；
2. 经过 ProjectOS 过滤的文件检索；
3. 需要执行或写入的工具；
4. 具备宿主机、浏览器或外部网络影响的工具。

每一步都应使用独立 capability、独立审计字段和独立回滚开关，不应一次性开放全部
provider tools。

## 当前实现边界

Responses 适配器只负责协议转换、流式约束和 provider builtin 的默认拒绝；工具来源、
授权和分层暴露仍由 ProjectOS 的 `ToolGateway`、`ToolAccessPolicy` 和
`ProjectOSTool` 负责。本文件只维护未来扩展项，不改变当前默认安全边界。

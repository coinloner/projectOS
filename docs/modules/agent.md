# Agent 模块

`app.agent` 定义 Workflow 调度的单节点智能执行单元。每次节点执行都会创建一个 CrewAI Agent，获得当前 domain 已授权的工具，并返回标准 `AgentResult`。

## 执行边界

```text
GraphRunner -> AgentRegistry -> BaseAgent -> ToolGateway -> CrewAI Agent + Task
```

`BaseAgent` 负责构造 CrewAI Agent/Task、取得工具并转换最终结果。它不注册工具、不控制 MCP 授权、不调度其他节点、不直接操作 Docker，也不维护跨 `run()` 的会话状态。

## AgentRegistry

`AgentRegistry` 同时保存公开的 `AgentDefinition` 和内部 factory：

- Planner 只读取 `id`、`domain`、`description`、`output_key`。
- GraphRunner 才可通过 `create(agent_id)` 调用 factory。
- `ExecutionPlan` 只携带 Agent id，不携带 Python 类、factory 或 Tool。

## 当前领域 Agent

| Agent | domain | 主要输出 | 能力边界 |
|---|---|---|---|
| `RequirementAgent` | requirement | `requirement.md` | 需求文档读写 |
| `ArchitectureAgent` | architecture | `architecture.md` | 读取 requirement，写架构文档 |
| `ArchitectureContractAgent` | architecture_contract | `.projectos/architecture/project-contract.json` | 读取已发布架构，生成唯一项目合同（分层、接口、Wave 和文件实现单元） |
| `TaskAgent` | task | `tasks.md` | 读取授权 ArtifactRef，写任务暂存并创建候选；不能直接发布 |
| `BootstrapAgent` | bootstrap | `environment.md`、runtime 声明 | 只能声明 profile/依赖，不能安装或运行 Docker |
| `CodeAgent` | code | `workspace/`、`implementation.md` | 读前置 artifact，受限读写 workspace；不能执行命令 |
| `CodeIntegrationAgent` | code_integration | `implementation` 合并摘要 | LLM 只审核已有 ChangeSet；Git 服务负责确定性合并；不能写业务代码 |
| `TestAgent` | test | `workspace/tests/`、`tests.md`、SandboxEvidence | 写测试，只能请求固定 sandbox check；原始结果由系统记录 |
| `ReviewAgent` | review | `review.md` | 受限只读项目、runtime 摘要和当前 Trace sandbox evidence，并调用确定性质量策略 |

## AgentResult

Agent 的最终输出被解析为两种正常状态：

- `completed`：包含最终文本，GraphRunner 转成完成的 `NodeResult`。
- `needs_capability`：严格 JSON 的外部能力请求。Agent 不会连接 MCP；Runner 只查候选 source 并返回等待/阻塞状态。

CrewAI 执行异常由 GraphRunner 转为失败节点。Docker 测试结果已成为结构化 `SandboxEvidence`；setup failure 会继续进入 Review 形成阻塞结论，真实测试失败仍按失败策略进入有限重试或 PlanPatch。

`CodeIntegrationAgent` 的 LLM 输出只能是结构化 Integration Review：`approve`、`reject` 或
`needs_adapter`。适配请求只有在对应适配文件已经存在于授权 ChangeSet 时才可继续；否则必须
重新生成实现单元。IntegrationAgent 没有写文件工具，也不会生成补丁或业务逻辑。
其中 `findings` 必须携带 `severity`（`blocker/error/warning/info`）；只有显式 `blocker` 才能
表达集成阶段的模型阻断意见，最终合并仍由确定性 Git Policy 决定。
代码集成审核发生在测试和最终 Review 之前，只负责审查已有 ChangeSet 的边界、冲突和可合并性；
不得把后续节点负责的 environment.md、tests.md、review.md 或运行证据当作当前合并前置条件。

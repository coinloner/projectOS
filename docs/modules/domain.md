# Domain 模块

`app.domain` 是 ProjectOS 的领域能力层。每个 domain 使用相同的目录形状：

```text
app/domain/<domain>/
  service.py    本地确定性能力，不依赖 CrewAI、Gateway 或 Planner
  tools.py      ToolSet 适配器、ToolDef 与 Gateway 注册入口
```

Agent 通过 `ToolGateway` 取得对应 ToolSet；ToolSet 再委托 Service 完成受限文件操作或受控执行请求。这样领域逻辑不会与 LLM 运行时、工具授权或图调度耦合。

## 统一能力表

| Domain | Service 的职责 | Agent 可用能力 | 禁止能力 | 持久化输出 |
|---|---|---|---|---|
| requirement | 管理需求文档 | 保存、加载 `requirement.md` | 读取其他 artifact、写 workspace | `requirement.md` |
| architecture | 管理架构文档 | 读取 requirement，保存 architecture | 写 workspace、运行环境 | `architecture.md` |
| task | 管理实施任务清单 | 读取 requirement/architecture，保存 tasks | 拆分 GraphRunner 状态、写 workspace | `tasks.md` |
| bootstrap | 管理运行时声明 | 读取前置 artifact，声明 profile/依赖，保存环境报告 | 安装依赖、联网、Docker 执行 | `runtime.yaml`、可选 `requirements.in`、`environment.md` |
| code | 管理实现交接和代码编辑 | 读取前置 artifact，读写允许的 workspace 文件，读取 runtime 摘要 | Docker、shell、依赖安装 | `workspace/`、`implementation.md` |
| test | 管理测试、测试报告与 Docker 执行证据 | 读取前置 artifact，写 `workspace/tests/`，请求固定 unit check | 写普通源码、任意命令、Docker 参数 | `workspace/tests/`、`tests.md`、当前 Trace 的 `SandboxEvidence` |
| review | 管理交付审查 | 受限读取 artifact、workspace、runtime 摘要与当前 Trace evidence，保存审查 | 修改 workspace、运行命令或修改 evidence | `review.md` |

## 输入输出边界

每个 domain 只能读取明确列出的前置 artifact，且只能写入自己的固定 Markdown 输出。普通 workspace 写入仅授予 Code；测试目录写入与 sandbox 执行仅授予 Test；Review 是只读。

这不是为了让 Agent 无法协作，而是为了把交接点固定成可审查的项目产物。后续需要扩展能力时，应先判断属于哪个 domain，再增量添加 ToolSet，而不是给所有 Agent 增加共享文件或 Shell 权限。

## Task Domain 的过渡定位

当前 Task domain 负责生成面向人和后续节点的 `tasks.md`。Planner 已创建和调度结构化 `WorkItem`，因此 `tasks.md` 不再是控制面事实来源；它保留为可阅读的实施计划和交接说明。后续可改为由 WorkItem 确定性渲染，减少一次 LLM 转写。

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
| architecture | 管理架构文档和架构试点的 staged/candidate 操作 | 独占节点读取 requirement、保存 architecture；分区节点写唯一 staged slot；集成节点创建候选 | 写 workspace、运行环境、直接发布候选 | `architecture.md`、`.projectos` 中的 staged/candidate/revision |
| task | 管理实施任务清单 | 读取受信 ArtifactRef，写入暂存并创建 tasks candidate | 拆分 GraphRunner 状态、写 workspace、直接发布根目录 | `tasks.md` |
| bootstrap | 管理运行时声明 | 读取前置 artifact，声明 profile/依赖，保存环境报告 | 安装依赖、联网、Docker 执行 | `runtime.yaml`、可选 `requirements.in`、`environment.md` |
| code | 管理实现交接和代码编辑 | 分区 CodeAgent 读取授权输入并写自己的 Git task worktree；Integration 按 ChangeSet 做三方合并并发布 `workspace/` | Docker、shell、依赖安装；分区节点不能直接写正式 workspace 或任意 Git | `.projectos/runs/<trace>/git/`、`workspace/`、`implementation.md` |
| test | 管理测试、测试报告与 Docker 执行证据 | 读取前置 artifact，写 `workspace/tests/`，请求固定 unit check | 写普通源码、任意命令、Docker 参数 | `workspace/tests/`、`tests.md`、当前 Trace 的 `SandboxEvidence` |
| review | 管理交付审查 | 受限读取 artifact、workspace、runtime 摘要与当前 Trace evidence，保存审查 | 修改 workspace、运行命令或修改 evidence | `review.md` |

## 输入输出边界

每个 domain 只能读取明确列出的前置 artifact，且只能写入自己的固定 Markdown 输出。普通 workspace 写入仅授予 Code；测试目录写入与 sandbox 执行仅授予 Test；Review 是只读。

Architecture 是当前唯一采用两层产物的试点 domain。普通独占架构节点仍使用
`load_artifact/save_architecture`；并行分区和集成节点使用 `ExecutionToolSetSource`，工具
由 `ExecutionContext.execution_mode` 限制为 `load_architecture_input`、
`write_staged_architecture` 或 `create_architecture_candidate`。没有 Architecture Agent 拥有
直接 promote 的工具。

Code 的并行边界采用资源级隔离：`backend` 和 `frontend` WorkItem 分别拥有自己的
Git branch/worktree，写入后形成 ChangeSet JSON。Integration 不接收任意 Git 参数，
只根据 `ExecutionContext.input_refs` 加载指定 ChangeSet，先由 `GitCodeIntegrationPolicy`
检查分区、baseline 和路径边界，再按 WorkItem ID 的固定顺序三方合并。合并成功后，控制面
只将提交中 `workspace/` 的变更复制到正式 workspace；Agent 从未获得正式目录或 Git CLI 权限。

Git worktree 不会机械套用到所有 domain。Requirement、Bootstrap 和 Review 仍是
单节点固定产物，使用 `ArtifactToolSet` 的单写入边界即可；Task、Architecture 已通过 staged
candidate 和 quality gate 实现隔离发布。TestAgent 是下一个有价值的迁移对象：
它可以在 Code merge commit 的测试 worktree 中写入 `workspace/tests/` 并运行 sandbox，
通过后再由控制面提交或发布测试变更。这个迁移必须和 SandboxController 的“指定 worktree
运行”能力一起完成，否则测试分支无法验证自身内容。

这不是为了让 Agent 无法协作，而是为了把交接点固定成可审查的项目产物。后续需要扩展能力时，应先判断属于哪个 domain，再增量添加 ToolSet，而不是给所有 Agent 增加共享文件或 Shell 权限。

## Task Domain 的过渡定位

当前 Task domain 负责生成面向人和后续节点的 `tasks.md`。它与 Code/Architecture 一样使用
`PARTITIONED -> INTEGRATION -> QUALITY_GATE` 标准产物链路：TaskAgent 只能读取任务输入包列出的
引用并写入暂存或候选，根目录发布由 Runner 的确定性质量门完成。Planner 已创建和调度结构化
`WorkItem`，因此 `tasks.md` 不再是控制面事实来源，只作为可阅读的实施计划和交接说明。

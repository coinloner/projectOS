# ProjectOS MVP Status

本页记录当前代码实际具备的能力，避免把设计意图误当成已完成能力。

| 模块 | 已实现 | 当前限制 | 下一步 |
|---|---|---|---|
| Project | 项目根目录、`workspace/`、`project.yaml`、默认 `runtime.yaml` | 项目级生命周期状态未统一持久化 | 统一项目运行记录 |
| API / Application | FastAPI 项目创建、受控 Workflow 异步启动、Trace/事件查询、checkpoint 校验后的 `/resume`；Container 统一组装运行时 | 仅进程内线程池，无认证、外部队列和租约恢复 | 持久化运行队列与跨进程 worker |
| Planner v1 | 受控上下文、重复 Agent WorkItem、JSON 草案校验、Repair Plan | 无跨运行状态恢复；修复质量仍依赖 prompt | Repair policy 迭代 |
| Workflow | 可复用 Template、Blueprint、默认依赖经验；`TemplateCompiler` 已支持 `architecture_compact` 与 `architecture_parallel` 受控模板 | 复杂度尚不能自动选择合适模板；普通模板仍是动态草案路径 | 自动复杂度分类与更多可安全分区模板 |
| Orchestration | WorkItem DAG、有界并发、Trace 计划/事件/Evidence、版本化 checkpoint 与受校验恢复、失败归因与有限重试；架构和代码分区具备资源隔离、集成和质量门授权 | 恢复只覆盖固定计划，TestAgent 仍直接写入正式 `workspace/tests/` | 可变 DAG 状态机与测试 worktree |
| Domain Agents | Requirement、Architecture、Task、Bootstrap、Code、Test、Review | 领域 prompt 和产出质量仍是基础版本；TestAgent 尚未迁移到独立测试 worktree | 按真实项目案例迭代工具与 policy |
| Local ToolSet | domain 隔离、artifact 读写限制、workspace 写入限制；架构 staged/candidate 工具按执行模式隐藏 | 两层产物只覆盖 architecture Markdown | 迁移其他适合并行的 Markdown domain |
| Artifact Repository | architecture staged output、candidate、IntegrationReport、质量门 promotion 和版本化兼容投影 | 未接 LLM Review 或接口语义检查；代码交付使用独立 Git ChangeSet | 扩展统一产物质量报告 |
| Code Parallel | backend/frontend Git task worktree、ChangeSet、路径隔离、Policy 三方合并并发布到正式 workspace | 目前只覆盖两个固定代码分区；冲突会阻止发布，未支持受限冲突解决 | 增加代码变更质量规则与失败返工 |
| ToolGateway | 静态本地注册、动态 source 授权、CrewAI 适配 | 真实 MCP client 未接入 | MCP connector 与审批恢复 |
| Sandbox | Docker 受控 unit 检查、`runtime-smoke` 入口组装探针、`SandboxEvidence` 持久化；`python-stdlib` unit 已真实跑通 | `health_path` 响应级探测尚未接入；Docker credential/daemon 仍是主机前置条件 | 增加受信 health/readiness smoke 与更完整 check catalog |
| Dependencies | `python-pip` 声明、依赖格式校验、hash wheel cache resolver | 需要所有者显式批准；通过 capabilities approve + resume 接入 | 外部队列和跨进程授权持久化 |
| Review | 可读取受限 artifact、runtime 摘要、workspace 与当前 Trace 原始证据 | 未把失败证据转成修复决策 | 接入 PlanPatch |
| Quality / Memory | Trace 级事件 MemoryStore、raw/temporary/working/episodic/durable 分层、candidate 生命周期、SQLite/FTS5 与可选向量混合召回、受控 durable 跨 Trace 召回、预算化 Prompt、运行摘要校验、checkpoint、候选审批/过期控制面和 Memory API | durable 候选仍依赖外部人工或 Review 调用控制接口 | 接入 Review/人工审批 UI 和归档策略 |

## MVP 验收标准

完成下面五项后，ProjectOS 可以作为“动态编排多 Agent 项目交付”的完整 MVP 展示：

1. Planner 以带来源的结构化 WorkItem 表达任务和依赖，且 Trace 可追溯。
2. Test 将 Docker 结果保存为结构化 `SandboxEvidence`，Review 可读取原始结果。
3. 失败证据可驱动 Planner 创建一次或多次受限修复步骤。
4. 系统在通过、达到重试上限或缺少批准时进入明确终态。
5. 一个示例项目能展示需求到实现、失败、修复、重测、Review 的完整记录。

## 当前最短开发路径

```text
PlanPatch from SandboxEvidence
  -> GraphRunner pause / replan / resume
  -> Code/Test retry policy
  -> terminal delivery report
```

MCP、Memory、更多 runtime profile 与复杂 Workflow 模板都应建立在这条路径完成之后。

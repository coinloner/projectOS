# ProjectOS 文档索引

文档按阅读目的组织，不按历史开发顺序组织。

## 快速理解当前系统

1. [一次请求的实际运行链路](architecture/request-trace.md)
2. [系统模块与边界](architecture/system-map.md)
3. [MVP 当前完成度和闭环缺口](roadmap/mvp-status.md)
4. [总体架构](ARCHITECTURE.md)
5. [路线图](ROADMAP.md)

## 模块说明

| 文档 | 内容 |
|---|---|
| [Agent](modules/agent.md) | 七个领域 Agent、Registry 和 CrewAI 边界 |
| [Domain](modules/domain.md) | 七个 domain 的本地能力、输入输出和权限边界 |
| [Workflow](modules/workflow.md) | Template、Blueprint 与默认依赖经验 |
| [Orchestration](modules/orchestration.md) | WorkItem、计划执行、运行状态与 Trace |
| [Sandbox](modules/sandbox.md) | Docker 执行隔离、依赖缓存与安全策略 |
| [Runtime](modules/runtime.md) | runtime manifest、profile 与受控状态摘要 |
| [Memory](modules/memory.md) | Trace 级会话记忆、执行上下文与恢复参考 |
| [ToolGateway](modules/tool_manager.md) | ToolSet、动态 source、Catalog 与访问策略 |
| [LLM](modules/llm.md) | Provider 预设、环境变量和 CrewAI LLM 工厂 |
| [Project](modules/project.md) | 项目目录和项目元信息 |

## API 契约

| 文档 | 内容 |
|---|---|
| [Agent API](api/agent-api.md) | `BaseAgent`、`AgentRegistry`、`AgentResult` |
| [Domain API](api/domain-api.md) | 七个 domain 的 service、ToolSet 和注册入口 |
| [Planner API](api/planner-api.md) | `PlanDraft`、`PlanningContext`、`PlannerService` |
| [Memory API](api/memory-api.md) | `MemoryStore`、`MemoryView` 和运行记忆接口 |
| [Workflow API](api/workflow-api.md) | Template、Blueprint 与默认依赖经验 |
| [Orchestration API](api/orchestration-api.md) | WorkItem、ExecutionPlan、GraphRunner 与 Trace |
| [Sandbox API](api/sandbox-api.md) | `SandboxController`、结果和依赖解析 |
| [Runtime API](api/runtime-api.md) | `RuntimeManifest`、Profile 和状态快照 |
| [ToolGateway API](api/tool_manager-api.md) | 本地 ToolSet、动态 source 与 CrewAI 工具暴露 |
| [LLM API](api/llm-api.md) | Provider 配置与 `build_llm()` |
| [Project API](api/project-api.md) | `Project` 生命周期接口 |
| [Git Repository API](api/git-repository-api.md) | ProjectOS 控制面管理的基线、任务分支、worktree 和三方合并 |

`Requirement` 不再拥有单独的文档树。它是七个 domain 中的一个，和 Architecture、Task、Bootstrap、Code、Test、Review 使用同一套记录结构。

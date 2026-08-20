# ProjectOS

ProjectOS 是一个面向软件交付的半托管多 Agent 编排平台。它把需求、架构、任务拆分、代码分区、测试和审查组织成可追踪的 WorkItem DAG，并通过 Policy、Artifact、Sandbox 和 Trace 把模型输出限制在受控边界内。

## 核心链路

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
                         -> Planner -> GraphRunner -> Trace / Artifact / Memory
```

当前实现包含：

- 结构化 `ExecutionPlan`、WorkItem 依赖和有界并发调度。
- 统一 Agent/Tool/Workflow 注册与执行边界，TaskAgent、CodeAgent 使用标准执行模式。
- Artifact staged/candidate/promotion 流程，以及代码分区的 Git worktree/ChangeSet 合并。
- Docker SandboxEvidence、失败归因、有限重试和 Repair Plan。
- Trace 级 Memory：分层事件、FTS5/可选向量召回、预算化上下文、durable 审批。
- 版本化 checkpoint 和受校验的 `/resume` 断点恢复。
- FastAPI 控制面：项目创建、受控 Workflow 启动、Trace/Memory 查询和恢复。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

启动 API：

```bash
.venv/bin/uvicorn app.api.asgi:app --reload
```

默认 API 地址为 `http://127.0.0.1:8000`，可以从 `/docs` 查看 OpenAPI 控制面。

## 重要边界

Memory 只提供运行连续性和受控召回，不替代 Policy、Artifact 或 Trace 的权威事实。Agent 不能自行批准外部工具、依赖、durable Memory 或正式产物发布。恢复流程会重新校验计划、Trace 和 checkpoint，失败或半完成节点不会被当作已完成节点跳过。

## 文档

- [文档索引](docs/README.md)
- [总体架构](docs/ARCHITECTURE.md)
- [MVP 完成度](docs/roadmap/mvp-status.md)
- [一次请求的实际运行链路](docs/architecture/request-trace.md)
- [Orchestration API](docs/api/orchestration-api.md)
- [Memory API](docs/api/memory-api.md)

## 当前限制

这是一个可演示的 MVP 基础架构：后台执行器仍是进程内线程池，尚未提供认证、外部队列租约、真实 MCP connector 和完整的测试 worktree 隔离。生产化前还需要补齐这些运行时能力及更严格的质量评估。

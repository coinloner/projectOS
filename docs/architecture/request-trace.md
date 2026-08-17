# ProjectOS Request Trace

本页描述一次“创建一个 Python 项目”的实际调用顺序。它是理解当前系统最短的路径。

## 0. 组装系统

`main.py` 创建或加载一个 `Project`，然后完成四件事：

1. 为七个 domain 注册本地 ToolSet。
2. 向 `AgentRegistry` 注册七个 `AgentDefinition` 和各自 factory。
3. 注册 `project_delivery_template()` 作为纯 Workflow 经验。
4. 创建 `PlannerService` 和 Orchestration 层的 `GraphRunner`。

项目创建时会建立 `workspace/`，并写入默认的 `runtime.yaml`：

```yaml
version: 1
profile: python-stdlib
```

## 1. 规划

```text
goal
  -> PlanningContext.build()
  -> CrewAIPlannerRuntime.generate()
  -> PlanDraft.parse()
  -> PlanValidator.validate()
  -> DependencyPolicy.resolve()
  -> ExecutionPlan
```

Planner 看到的是控制面摘要：目标、已注册 Agent 的 `id/domain/description/output_key`、完整模板节点和默认依赖、artifact 是否存在、workspace 文件计数与 runtime 摘要。它看不到源代码、artifact 正文、factory、工具对象、Docker 或 MCP client。

Planner 输出不可信 JSON。它用临时 `ref` 描述步骤间依赖；`PlanValidator` 生成可信 WorkItem id，并合并 Planner、Template 与系统三类依赖。一次错误草案可请求 LLM 修复一次。

## 2. 调度七个节点

默认模板表达的依赖关系如下：

```text
requirement
  -> architecture
  -> tasks
  -> environment (Bootstrap)
  -> implementation (Code)
  -> tests (Test)
  -> review
```

GraphRunner 只根据 `ExecutionPlan` 的 WorkItem DAG 找到 ready item。对每个 WorkItem，它通过 `AgentRegistry.create(agent_id)` 创建一个新的 Agent 实例，并把总体目标、WorkItem id、当前目标与可用前置 artifact key 组成 task 文本。同时它创建 `ExecutionContext(trace_id, work_item_id, agent_id)`，只通过 ToolGateway 绑定到本次 Agent 的工具对象。

Agent 不会直接获得前置文件正文。它必须使用自己的 `load_artifact` 工具按需读取，这让每个 domain 的读取范围可以单独限制。

## 3. 一个 Agent 如何调用本地工具

以 CodeAgent 为例：

```text
GraphRunner
  -> CodeAgent.run(task)
  -> ToolGateway.tools_for("code")
  -> CrewAI Agent + Task
  -> CrewAI 选择 load_artifact / write_workspace_file / save_implementation
  -> ProjectOSTool 参数验证
  -> ToolSetSource.execute()
  -> CodeService
  -> ArtifactToolSet 或 WorkspaceToolSet
  -> projects/<project>/workspace/ 和 implementation.md
```

CodeAgent 有 workspace 文件读写能力，但没有 shell、Docker、网络或依赖安装能力。它应将实现摘要保存到 `implementation.md`，供 Test 与 Review 使用。

## 4. Bootstrap 和 Docker 测试

BootstrapAgent 可以调整 `runtime.yaml`，并在选择 `python-pip` 时写入受限格式的 `requirements.in`。这只是声明，不会触发安装。

TestAgent 写入 `workspace/tests/` 后，只能调用无参数的 `run_sandbox_check`：

```text
GraphRunner-created ExecutionContext
  -> ExecutionToolSetSource.run_sandbox_check()
  -> TestService.run_sandbox_check()
  -> SandboxController.run_check(project_path, "unit")
  -> RuntimeManifest.load()
  -> SandboxPolicy.create_spec()
  -> DockerSandboxProvider.run_check()
  -> SandboxResult
  -> TraceStore.record_sandbox_evidence()
  -> .projectos/runs/<trace_id>/evidence/<evidence_id>.json
```

对 `python-stdlib`，固定检查为：

```text
python -m unittest discover -s tests -v
```

它在 Docker 容器的 `/workspace` 中执行。Agent 无法修改命令、镜像、挂载、网络或容器权限。

## 5. Trace、结果与结束

`PlannerService` 为每次计划创建一个 `trace_id`，并复用项目稳定的 `requirement_id`。GraphRunner 将 `plan.json` 与 WorkItem 事件写入：

```text
projects/<project>/.projectos/
  requirement.json
  requirements/revision-<n>.md
  runs/<trace_id>/trace.json
  runs/<trace_id>/plan.json
  runs/<trace_id>/events.jsonl
  runs/<trace_id>/evidence/<evidence_id>.json
```

每个 Agent 最终返回 `AgentResult`。GraphRunner 将它转换为 `NodeResult` 并记录进本次 `RunState` 与 Trace：

- 成功：把节点最终文本放入 `RunState.artifacts[output_key]`。
- 能力缺口：查询候选动态 source，返回 `waiting_for_capability_approval` 或 `blocked`。
- 异常：立即返回 `failed`。

所有节点成功后，GraphRunner 返回 `completed`。当前这表示“线性链路已全部运行”，不表示项目已经满足强交付标准。

## 6. 当前断点：为什么还不是完整闭环

当 Docker 测试结束时，原始 `SandboxResult` 会被保存为带 Trace、WorkItem 和 Agent 归属的 `SandboxEvidence`。TestAgent 仍可将可读摘要写入 `tests.md`，但 Review 用 `list_sandbox_evidence` 和 `load_sandbox_evidence` 读取原始结果。GraphRunner 目前不解释失败的业务含义，也不会把证据交给 Planner 生成修复步骤。

要形成闭环，需要在 Test 后加入如下状态转移：

```text
SandboxEvidence.failed
  -> Planner 读取结构化失败证据
  -> 创建或更新修复 WorkItem
  -> CodeAgent 修复 workspace
  -> TestAgent 再跑同一受控 check
  -> 达到通过、重试上限或 blocked
```

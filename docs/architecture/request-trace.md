# ProjectOS Request Trace

本页描述一次“创建一个 Python 项目”的实际调用顺序。它是理解当前系统最短的路径。

## 0. 组装系统

`main.py` 创建或加载一个 `Project`，然后完成四件事：

1. 为七个 domain 注册本地 ToolSet。
2. 向 `AgentRegistry` 注册七个 `AgentDefinition` 和各自 factory。
3. 注册 `project_delivery_template()` 作为 Planner 的流程经验。
4. 创建 `PlannerService` 和 `GraphRunner`。

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
  -> ExecutionPlan
```

Planner 看到的是控制面摘要：目标、已注册 Agent 的 `id/domain/description/output_key`、模板摘要、artifact 是否存在、workspace 文件计数与 runtime 摘要。它看不到源代码、artifact 正文、factory、工具对象、Docker 或 MCP client。

Planner 输出不可信 JSON。`PlanValidator` 会拒绝未知 Agent、重复 Agent、非法依赖、循环图，以及缺少 Bootstrap/Code/Test 前置条件的计划；一次错误草案可请求 LLM 修复一次。

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

GraphRunner 只根据 `ExecutionPlan` 的 DAG 找到 ready node。对每个节点，它通过 `AgentRegistry.create(agent_id)` 创建一个新的 Agent 实例，并把总体目标、当前目标与可用前置 artifact key 组成 task 文本。

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
TestToolSet.run_sandbox_check()
  -> TestService.run_sandbox_check()
  -> SandboxController.run_check(project_path, "unit")
  -> RuntimeManifest.load()
  -> SandboxPolicy.create_spec()
  -> DockerSandboxProvider.run_check()
  -> SandboxResult
```

对 `python-stdlib`，固定检查为：

```text
python -m unittest discover -s tests -v
```

它在 Docker 容器的 `/workspace` 中执行。Agent 无法修改命令、镜像、挂载、网络或容器权限。

## 5. 节点结果与结束

每个 Agent 最终返回 `AgentResult`。GraphRunner 将它转换为 `NodeResult` 并记录进本次 `RunState`：

- 成功：把节点最终文本放入 `RunState.artifacts[output_key]`。
- 能力缺口：查询候选动态 source，返回 `waiting_for_capability_approval` 或 `blocked`。
- 异常：立即返回 `failed`。

所有节点成功后，GraphRunner 返回 `completed`。当前这表示“线性链路已全部运行”，不表示项目已经满足强交付标准。

## 6. 当前断点：为什么还不是完整闭环

当 Docker 测试失败时，`SandboxResult` 只会以文本形式返回给 TestAgent，由它写进 `tests.md`。GraphRunner 不理解测试失败的业务含义，也不会把失败证据交给 Planner。因此执行会继续到 Review 或在节点异常时直接结束，而不会稳定地产生修复任务并重试。

要形成闭环，需要在 Test 后加入如下状态转移：

```text
SandboxEvidence.failed
  -> Planner 读取结构化失败证据
  -> 创建或更新修复 WorkItem
  -> CodeAgent 修复 workspace
  -> TestAgent 再跑同一受控 check
  -> 达到通过、重试上限或 blocked
```

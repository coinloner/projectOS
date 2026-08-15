# ProjectOS 架构

ProjectOS 是一个以 LLM Planner 为控制面的多 Agent 项目交付原型。它把“规划、领域产出、受控工具、隔离执行”拆成独立层，目标是让 Agent 能在受限范围内推进一个项目，而不是直接把宿主机权限交给模型。

阅读实际运行过程请先看 [request-trace](architecture/request-trace.md)，模块边界和状态见 [system-map](architecture/system-map.md)，当前完成度见 [mvp-status](roadmap/mvp-status.md)。

## 分层架构

```text
用户目标
  -> Planner v0
  -> ExecutionPlan
  -> GraphRunner
  -> Domain Agent (CrewAI)
  -> ToolGateway
  -> ToolCatalog + ToolAccessPolicy
  -> Local ToolSet / MCP ToolSource
  -> 项目产物、workspace、SandboxController
  -> Docker Sandbox
```

| 层 | 当前职责 | 不负责 |
|---|---|---|
| Planner v0 | 从受控上下文生成并校验一次 `ExecutionPlan` | 写项目文件、调用工具、直接执行节点 |
| Workflow | 保存流程经验，调度计划节点并记录运行内状态 | 领域业务、测试判定、质量评价 |
| Domain Agent | 用 LLM 完成一个领域节点，并通过受限工具读写产物 | 跨节点调度、工具授权、Docker 控制 |
| ToolGateway | 按 domain 和授权状态把工具包装为 CrewAI 工具 | 业务执行、工具循环、依赖安装 |
| Domain Service | 实现本地读写和受限项目操作 | LLM、CrewAI、计划编排 |
| Runtime / Sandbox | 将运行时声明变成固定、隔离的 Docker 检查 | 接收 LLM 的任意命令 |

## 当前默认交付链路

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
```

这七个节点都是可注册、可被 Planner 选择的 Agent。`project_delivery_template()` 提供默认顺序和依赖关系；Planner v0 可以在注册合同和校验规则范围内生成计划，但同一个 Agent 在一次计划中最多出现一次。

各节点的磁盘产物为：

```text
requirement.md -> architecture.md -> tasks.md -> environment.md
workspace/     -> implementation.md -> tests.md -> review.md
```

其中 `workspace/` 是生成项目的源代码与测试目录；其余 Markdown 是交接、审查和解释用的项目产物。`RunState.artifacts` 则只保存一次 GraphRunner 运行内的节点返回文本，不是持久化的工作流状态。

## 工具与权限模型

本地 ToolSet 是静态注册的确定性能力，按 domain 默认暴露。MCP 是动态来源，必须先注册、再由上层按 source 激活，才会发现并暴露远端工具。

```text
ToolDef              工具声明：名称、描述、参数 JSON Schema
ToolSource           工具执行来源：本地 ToolSet 或未来的 MCP
ToolCatalog          记录声明与来源的注册关系
ToolAccessPolicy     决定来源是否对当前 Agent 可见
ToolGateway          生成 CrewAI BaseTool
CrewAI               管理 LLM tool-calling loop 和参数验证
```

`ToolDef` 不保存 Python 函数；函数或远端调用逻辑属于 `ToolSource`。因此本地工具与 MCP 可以使用同一套发现、授权和 CrewAI 适配边界。

当前 MCP connector 和“批准后恢复执行”尚未实现。查询候选 source 不会连接 MCP，也不会自动扩大 Agent 权限。

## 执行安全模型

`BootstrapAgent` 只声明 `runtime.yaml`、可选 `requirements.in` 和环境报告。它不能安装依赖、调用 Docker 或联网。

`TestAgent` 只能写 `workspace/tests/` 并请求固定的 `unit` 检查。`SandboxController` 读取不可信的运行时声明，经 `SandboxPolicy` 转成受信任 `SandboxSpec`，最后由 Docker 执行。默认容器无网络、只读、非 root、无 Linux capabilities，并且只读挂载被生成项目的 `workspace/`。

当前可真实运行的 profile 是 `python-stdlib`。`python-pip` 已具备声明和 wheel cache 策略，但依赖下载必须经项目所有者明确批准，且尚未接入 Workflow 的暂停/恢复流程。

## 当前闭环边界

目前系统能完成一次线性项目交付：从需求走到 Docker 测试和 Review。它尚不是可自动纠偏的闭环，因为：

- `TaskAgent` 仍输出 Markdown 任务清单，尚未被 Planner 拆成结构化 `WorkItem`。
- `SandboxResult` 尚未持久化为 Planner 可消费的结构化执行证据。
- GraphRunner 遇到节点失败会结束，尚未进行局部重规划、代码修复和重测。
- `Review` 读取的是测试报告与项目文件，不是稳定的原始执行证据。

下一阶段的目标不是增加更多 Agent，而是完成“测试失败 -> 证据 -> Planner 重排 -> Code 修复 -> Docker 重测 -> 交付判定”的最小循环。

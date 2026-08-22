# ProjectOS 架构

ProjectOS 是一个以 LLM Planner 为控制面的多 Agent 项目交付原型。它把“规划、领域产出、受控工具、隔离执行”拆成独立层，目标是让 Agent 能在受限范围内推进一个项目，而不是直接把宿主机权限交给模型。

阅读实际运行过程请先看 [request-trace](architecture/request-trace.md)，模块边界和状态见 [system-map](architecture/system-map.md)，当前完成度见 [mvp-status](roadmap/mvp-status.md)。

## 分层架构

```text
用户目标
  -> FastAPI / CLI
  -> Application RunService
  -> ProjectOSContainer / Domain Module installers
  -> WorkflowTemplate（经验参考）
  -> Planner v0
  -> ExecutionPlan
  -> Orchestration / GraphRunner
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
| API / Application | 提供项目级运行入口、组装 Container、提交后台运行 | 决定 Agent 权限、实现领域业务 |
| Workflow | 保存可复用流程经验与默认依赖 | 计划执行、运行状态、Trace |
| Orchestration | 调度 WorkItem、绑定可信执行身份、记录 RunState/Trace/Evidence | 领域业务、流程模板选择、质量评价 |
| Domain Agent | 用 LLM 完成一个领域节点，并通过受限工具读写产物 | 跨节点调度、工具授权、Docker 控制 |
| ToolGateway | 按 domain 和授权状态把工具包装为 CrewAI 工具 | 业务执行、工具循环、依赖安装 |
| Domain Service | 实现本地读写和受限项目操作 | LLM、CrewAI、计划编排 |
| Runtime / Sandbox | 将运行时声明变成固定、隔离的 Docker 检查 | 接收 LLM 的任意命令 |

## 并行产物试点

架构 Markdown 已具备一个独立于默认交付链路的并行产物试点。它不允许多个 Agent
同时覆盖 `architecture.md`，而是把写入权限拆成分区、集成和质量门三个角色：

```text
PARTITIONED scope -> staged output -> INTEGRATION candidate -> QUALITY_GATE promote
```

`WorkItem.execution_mode`、`input_refs`、`output_slot` 与 `publish_target` 是系统创建的
执行授权。它们由 `GraphRunner` 绑定到 `ExecutionContext`，不出现在 Planner JSON 或工具
参数 schema 中。`ToolGateway` 会按该模式隐藏工具：分区节点只能写自己的 staged slot，
集成节点只能读获授权的冻结引用并创建候选，质量门则由 Runner 确定性执行发布。

完整的存储布局、对象和当前限制见 [artifact module](modules/artifact.md)。`architecture.md`
依旧是已发布版本的兼容投影，现有单节点流程不受这次试点影响。

`architecture_parallel` 已注册为第一个受控 Workflow。Planner 选择该模板后，
`TemplateCompiler` 自动生成 baseline、三个分区、integration 和 quality gate；这些权限字段
不接受 Planner 修改。没有匹配模板时仍可使用原来的独占架构节点。

## 当前默认交付链路

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
```

七个 domain Agent 都可注册和被 Planner 选择；但 TaskAgent 只能通过受控模板生成分区、集成和质量门 WorkItem。普通 Planner 路径仍由 `DependencyPolicy` 合并系统依赖。

各节点的磁盘产物为：

```text
requirement.md -> architecture.md -> tasks.md -> environment.md
workspace/     -> implementation.md -> tests.md -> review.md
```

其中 `workspace/` 是生成项目的源代码与测试目录；其余 Markdown 是交接、审查和解释用的项目产物。每次计划还有稳定 `trace_id`，其计划、事件和 Docker 证据保存在 `.projectos/runs/<trace_id>/`；`RunState.artifacts` 仍只保存一次进程内的节点返回文本。

## 工具与权限模型

本地 ToolSet 是静态注册的确定性能力，按 domain 默认暴露。MCP 是动态来源，必须先注册、再由上层按 source 激活，才会发现并暴露远端工具。

```text
ToolDef              工具声明：名称、描述、参数 JSON Schema、可选 execution_modes
ToolSource           工具执行来源：本地 ToolSet 或未来的 MCP
ToolCatalog          记录声明与来源的注册关系
ToolAccessPolicy     决定来源是否对当前 Agent 可见
ToolGateway          生成 CrewAI BaseTool
CrewAI               管理 LLM tool-calling loop 和参数验证
```

`ToolDef` 不保存 Python 函数；函数或远端调用逻辑属于 `ToolSource`。因此本地工具与 MCP 可以使用同一套发现、授权和 CrewAI 适配边界。

普通 `ToolSetSource` 只接收模型 schema 中的业务参数。少数需要追溯归属的工具使用 `ExecutionToolSetSource`；`GraphRunner` 创建 `ExecutionContext(trace_id, work_item_id, agent_id)` 并由 Gateway 绑定，模型看不到也不能伪造这些字段。

动态 source 默认不会发现或暴露；Trace 进入 `WAITING_FOR_CAPABILITY_APPROVAL` 后，调用
`GET /runs/{trace_id}/capabilities` 查询候选，再用 `POST /runs/{trace_id}/capabilities/approve`
在同一次恢复动作中激活 source 并继续 checkpoint。批准动作会写入 `capability_approved` 事件。

HTTP 接入层见 [API module](modules/api.md)。`ProjectOSContainer` 是唯一的运行时组合根：
Domain installer 在其中注册各自的 Tool 和 Agent，Workflow 也在此处注册；`main.py` 和
FastAPI 都不再维护重复的注册清单。

## 执行安全模型

`BootstrapAgent` 只声明 `runtime.yaml`、可选 `requirements.in` 和环境报告。它不能安装依赖、调用 Docker 或联网。

`TestAgent` 只能写 `workspace/tests/` 并请求固定的 `unit` 检查。`SandboxController` 读取不可信的运行时声明，经 `SandboxPolicy` 转成受信任 `SandboxSpec`，最后由 Docker 执行。默认容器无网络、只读、非 root、无 Linux capabilities，并且只读挂载被生成项目的 `workspace/`。

当前可真实运行的 profile 是 `python-stdlib`。`python-pip` 已具备声明和 wheel cache 策略；依赖下载仍必须经项目所有者明确批准，运行前可通过 runtime preflight 检查。

## 当前闭环边界

系统的目标闭环是从需求走到 Docker 测试和 Review；Docker 镜像和依赖缓存属于运行前置，缺失时
会产生受控 `setup_failed` 证据并由 Review 明确标记阻塞，而不是伪造通过。它尚不是可自动纠偏的闭环，因为：

- Planner 已使用结构化 `WorkItem`；`TaskAgent` 通过 `PARTITIONED -> INTEGRATION -> QUALITY_GATE`
  链路输出给人阅读的 `tasks.md`，不再使用旧的独占读写方式。
- `SandboxResult` 已作为带 Trace 和 WorkItem 归属的 `SandboxEvidence` 持久化；Review 可只读原始证据。
- GraphRunner 已可按失败类型有限重跑或请求 Repair Plan，并能从版本化 checkpoint 恢复固定计划中未完成的节点；当前主入口最多执行两轮修复计划，可变 DAG 状态机仍未完成。
- Repair Plan 只读取失败类型、证据 ID 与元数据；Code/Test 修复节点才接收受限 `FailurePackage`，其中程序输出明确视为不可信诊断数据。
- Test WorkItem 没有产生自身的 `SandboxEvidence` 时不能完成，Runner 只会在有限重跑后将其标记失败。

下一阶段的目标不是增加更多 Agent，而是完成“测试失败 -> 证据 -> Planner 重排 -> Code 修复 -> Docker 重测 -> 交付判定”的最小循环。

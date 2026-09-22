# ProjectOS Roadmap

路线图以可演示的完整闭环为优先级，不以模块数量为目标。当前状态的详细矩阵见 [mvp-status](roadmap/mvp-status.md)。

## 当前已可运行的纵向链路

```text
Requirement -> Architecture -> Task -> Bootstrap -> Code -> Test -> Review
```

- Planner v0 从已注册 Agent、完整模板依赖、artifact 元数据和 runtime 状态生成 WorkItem 计划。
- GraphRunner 同步执行 WorkItem DAG、写入 Trace 计划/事件，并处理失败和外部能力缺口的暂停结果。
- 七个 domain 已有受限本地 ToolSet，代码和测试可写入被生成项目的 `workspace/`。
- Test 通过 `SandboxController` 运行固定 Docker 检查并持久化 `SandboxEvidence`；Review 可读取当前 Trace 的原始结果。
- Trace 级 Memory 已记录目标、Planner/Agent/Tool 事件和 checkpoint；SQLite/FTS5、可选向量混合召回、运行摘要、预算化上下文组装以及 durable candidate 审批控制面已接入。
- MCP、动态依赖解析和运行恢复都不会由 Agent 自动触发。

## 原始八阶段与当前状态

路线按最初约定恢复为八个阶段。稳定性、观测和错误归因属于各阶段的横向验收条件，
不另起一个架构阶段。

1. **固定模板与 ProcessDefinition**：已完成。保留现有固定模板，新增流程阶段、合法转换、
   Agent/工具执行规则和规模限制。
2. **Semantic Registry 校验 Blueprint**：已完成。`SemanticRegistry` 集中登记字段的
   meaning/source/consumer/value_rules，`BlueprintValidator` 在动态扩展前执行层依赖、模块
   purpose、module_id 唯一性和未知依赖校验；Pydantic 仍负责 DTO 形状和类型硬门。
3. **DynamicPlanBuilder 生成 ModuleDesign**：已完成。Blueprint 的真实模块清单、业务目的、
   模块依赖和执行 wave 会动态生成 depth=1 WorkItem。
4. **动态生成 ImplementationDesign**：已完成。所有 ModuleDesign 完成后按真实模块清单生成
   depth=2 WorkItem，并在集成前校验接口 ownership、文件边界和 unit 依赖。
5. **ContractCompiler 生成 CodeAgent 节点**：已落地，正在进行真实链路验收。集成后的
   `ImplementationDesign` 可以确定性编译为单文件、分 wave 的 CodeAgent WorkItem。
6. **接入 Tasks、Environment、Test、Review**：部分完成。固定交付流程已有这些节点，动态
   合同编译和代码 wave 已接入；还需要用真实项目验证任务、环境、测试、审查能从动态架构结果
   连续执行并正确恢复。
7. **多项目类型真实 E2E**：未完成。需要至少覆盖不同模块数量、前后端、数据库、异步流程和
   依赖审批场景，并记录通过、阻塞和恢复证据。
8. **删除固定项目模板**：未开始。只有阶段 6、7 稳定并完成迁移验证后，才删除固定项目模板；
   在此之前模板只作为受控兼容适配器存在。

## 当前推进目标：阶段五到阶段七

1. 验证 `ArchitectureDesignBundle -> Project Contract -> CodeAgent WorkItem` 的真实传递，
   确保每个 unit、interface ownership、文件路径和 wave 语义不丢失。
2. 将动态编译结果与 Tasks、Environment、Test、Review 的依赖和产物 owner 对齐。
3. 让失败证据、局部重试、checkpoint 恢复和最终 Review 在同一动态计划中闭环。
4. 用确定性 Agent 先完成阶段五到阶段六的完整回归，再用真实 FHL Agent 连续运行多种项目类型，
   形成阶段七验证矩阵，而不是只用 Fake Agent 或单独的架构探针。

## MVP 后的扩展顺序

| 顺序 | 能力 | 原因 |
|---|---|---|
| 1 | Runtime profile：FastAPI、Node/Vitest | 扩展可交付项目类型 |
| 2 | 可配置且受控的 test discovery | 避免固定 `unittest tests/` 限制 |
| 3 | MCP connector 与 source 审批恢复 | 扩展外部能力，同时保持最小权限 |
| 4 | NodeQualityPolicy / QualityReport | 为节点产物添加可量化质量下限 |
| 5 | Memory | 受控跨 Trace 召回、摘要质量评估和归档；不改变 Policy/Artifact 的事实边界 |
| 6 | Workflow 模板沉淀与选择 | 将常见项目交付经验变成 Planner 可参考资产 |
| 7 | CLI / API / Dashboard | 把已有控制面变为可操作产品界面 |

## 不在当前 MVP 范围内

- 任意 Shell 工具或宿主机命令执行
- Agent 自动联网安装依赖
- Agent 自动批准 MCP、依赖或运行时权限
- 外部队列驱动的跨进程 worker 租约、自动跨项目长期记忆

这些能力会在执行证据、状态机和权限主体明确后逐步加入。

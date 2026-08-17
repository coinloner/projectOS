# ProjectOS MVP Status

本页记录当前代码实际具备的能力，避免把设计意图误当成已完成能力。

| 模块 | 已实现 | 当前限制 | 下一步 |
|---|---|---|---|
| Project | 项目根目录、`workspace/`、`project.yaml`、默认 `runtime.yaml` | 项目级生命周期状态未统一持久化 | 统一项目运行记录 |
| Planner v0 | 受控上下文、JSON 草案、一次修复、WorkItem 与三类依赖校验 | 每 Agent 每计划一次；无运行反馈重规划 | PlanPatch |
| Workflow | WorkItem DAG、同步 GraphRunner、Trace 计划/事件、能力缺口暂停 | RunState 不可恢复、无并发/重试 | 状态机与恢复接口 |
| Domain Agents | Requirement、Architecture、Task、Bootstrap、Code、Test、Review | 领域 prompt 和产出质量仍是基础版本 | 按真实项目案例迭代工具与 policy |
| Local ToolSet | domain 隔离、artifact 读写限制、workspace 写入限制 | 没有跨 domain 复合能力 | 保持小工具集，按需要增量扩展 |
| ToolGateway | 静态本地注册、动态 source 授权、CrewAI 适配 | 真实 MCP client 未接入 | MCP connector 与审批恢复 |
| Sandbox | Docker 受控 unit 检查；`python-stdlib` 已真实跑通 | 固定 unittest discovery，结果不持久化 | `SandboxEvidence` 与 check catalog |
| Dependencies | `python-pip` 声明、依赖格式校验、hash wheel cache resolver | 需要所有者显式批准；未连到 Workflow | 批准请求和 resume |
| Review | 可读取受限 artifact、runtime 摘要与 workspace | 主要读取 Markdown 报告，缺少原始证据 | 接入结构化 evidence |
| Quality / Memory | 尚未接入主链路 | 无质量下限或跨运行学习 | 在闭环稳定后增加 |

## MVP 验收标准

完成下面五项后，ProjectOS 可以作为“动态编排多 Agent 项目交付”的完整 MVP 展示：

1. Planner 以带来源的结构化 WorkItem 表达任务和依赖，且 Trace 可追溯。
2. Test 将 Docker 结果保存为结构化 `SandboxEvidence`。
3. 失败证据可驱动 Planner 创建一次或多次受限修复步骤。
4. 系统在通过、达到重试上限或缺少批准时进入明确终态。
5. 一个示例项目能展示需求到实现、失败、修复、重测、Review 的完整记录。

## 当前最短开发路径

```text
SandboxEvidence persistence
  -> GraphRunner pause / replan / resume
  -> Code/Test retry policy
  -> terminal delivery report
```

MCP、Memory、更多 runtime profile 与复杂 Workflow 模板都应建立在这条路径完成之后。

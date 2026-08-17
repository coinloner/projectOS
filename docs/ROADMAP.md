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
- MCP、动态依赖解析和运行恢复都不会由 Agent 自动触发。

## MVP 闭环：下一阶段

目标：让系统能依据真实测试结果完成有限次数的修复与再验证。

1. 增加 WorkItem 状态转移：测试失败后基于 `SandboxEvidence` 暂停给 Planner，生成修复工作项，再回到 Code/Test。
2. 定义终态：所有必需工作项完成、sandbox 通过、review 无阻塞项，或明确 `blocked`。
3. 为修复循环加入重试上限和每次尝试的 evidence 关联。
4. 为 `python-pip` 接入“所有者批准 -> DependencyResolver 建 wheel cache -> 恢复执行”的控制面流程。

## MVP 后的扩展顺序

| 顺序 | 能力 | 原因 |
|---|---|---|
| 1 | Runtime profile：FastAPI、Node/Vitest | 扩展可交付项目类型 |
| 2 | 可配置且受控的 test discovery | 避免固定 `unittest tests/` 限制 |
| 3 | MCP connector 与 source 审批恢复 | 扩展外部能力，同时保持最小权限 |
| 4 | NodeQualityPolicy / QualityReport | 为节点产物添加可量化质量下限 |
| 5 | Memory | 提供跨运行的上下文，而不污染当前计划状态 |
| 6 | Workflow 模板沉淀与选择 | 将常见项目交付经验变成 Planner 可参考资产 |
| 7 | CLI / API / Dashboard | 把已有控制面变为可操作产品界面 |

## 不在当前 MVP 范围内

- 任意 Shell 工具或宿主机命令执行
- Agent 自动联网安装依赖
- Agent 自动批准 MCP、依赖或运行时权限
- 并发图执行、跨进程计划恢复、跨项目长期记忆

这些能力会在执行证据、状态机和权限主体明确后逐步加入。

# ProjectOS 执行计划 v2：全设计编译、短垂直切片、逐段验收

> 日期：2026-09-27
> 目标：完成 ProjectOS 重构计划，并用可量化证据证明从 Requirement 到完整交付的闭环。
> Provider：`wanfa / gpt-6-sol / responses`
> 当前 Live Trace：`tr-2e4924e985cf`
>
> 本版本替代“直接恢复完整 Live L2 并等待最终结果”的执行方式。

## 1. 核心策略

不在“完整系统”和“任意局部组件”之间二选一，而是采用：

```text
完整 Requirement / Architecture / Contract 全量编译
                    ↓
       选择一个真实的垂直业务切片执行
                    ↓
       通过后再扩展到完整 L2 交付
```

这样保留整体设计的一致性，同时把每次实验控制在可观察、可归因的范围内。

### 1.1 不再采用的做法

- 不再把完整 Live L2 作为第一次验证。
- 不再让一次任务连续运行数小时后才汇报总体结果。
- 不再把 Provider 502/503、重构缺陷、生成项目缺陷混在一个失败结论中。
- 不再把 Replay 成功、workspace 中有代码或静态测试通过，写成 Live Trace 已完成。
- 不再用 Integration Review fallback 处理 CodeAgent 本身的 Provider 失败。

### 1.2 每个切片的硬规则

每个切片开始前必须记录：

- 目标和范围；
- 最长观察时间；
- 成功证据；
- 失败停止条件；
- 预计修改文件；
- 本切片不允许修改的范围。

每个切片结束后必须报告：

- 实际耗时；
- 实际修改文件和 Git commit；
- 运行命令与结果；
- 证据文件路径；
- 问题归因：重构、原始设计、生成代码、Provider 或运行环境；
- 下一步策略选项。

Provider 出现明确 502/503/timeout 时，最多进行一次有界重试；再次失败就停止当前切片并报告，不进入数小时盲重试。

## 2. 变更归因规则

所有后续修改按三类标记，避免混淆：

- `R`：ProjectOS 控制面/编排/重构修改；
- `F`：Live 生成项目的业务代码或合同修复；
- `V`：验证脚本、证据和测试修改。

`R`、`F`、`V` 尽量分开提交。每个证据文件必须声明其来源类别。

原始 Live 项目中的以下问题归类为 `F/原始设计或生成代码缺陷`，不能归咎于 ProjectOS 重构本身：

- `TaskManagementService` 与实际 `TaskService` 的模块/符号不一致；
- `/tasks`、`/api/tasks`、统计路径和状态路径漂移；
- consumer 根据 interface id 自行猜测 provider 路由。

以下问题归类为 `R/控制面缺陷`：

- 已完成产物与最新 checkpoint frontier 不一致；
- CodeAgent Provider 失败无法被短路、隔离或准确归因；
- 失败后恢复范围扩大到不相关节点；
- 没有在每个关键边界发布可审计证据。

## 3. 分阶段执行

### Phase 0：冻结基线和现状审计

**范围：** 只读检查，不启动新 Trace，不修改业务代码。

**检查：**

- ProjectOS 全量测试；
- `git diff --check`；
- `compileall`；
- failure matrix；
- 当前 Live Trace 状态、事件、checkpoint、workspace Git 历史；
- 历史失败证据仍存在。

**成功证据：**

```text
validation-evidence/baseline-*.json
validation-evidence/failure-matrix-*.json
validation-evidence/l1-closure-audit-*.json
```

**停止条件：** 发现状态源相互矛盾时先修正证据/状态模型，不继续长跑。

### Phase 1：Replay L0 语义闭环

**范围：** 不调用真实 Provider。

```text
Requirement
→ Blueprint
→ ModuleDesign
→ ImplementationDesign
→ Architecture Integration
→ Quality Gate
→ Contract Compiler
```

**必须证明：**

- 分层 Architecture checkpoint 可恢复；
- parent/dependency/ownership/binding 完整；
- `project-contract.json` 和 `implementation-plan.json` 可加载；
- `semantic_loop_completed=true`；
- 没有 unresolved refs、重复 ownership 或循环依赖。

**成功证据：** `replay-l0-*.json`。

**最长观察时间：** 15 分钟。失败时只修 L0 控制面，不进入 Code。

### Phase 2：Replay L1a 控制面垂直切片

**范围：** 使用完整合同，但只执行一个最小 CodeAgent 单元。

```text
完整 Contract
→ 一个 owned file
→ CodeAgent ChangeSet
→ Wave 0 merge
→ /health=200
```

**必须证明：**

- prompt 包含 canonical binding；
- CodeAgent 只写 owned file；
- ChangeSet 存在；
- Wave merge commit 存在；
- runtime 启动并返回 `/health=200`；
- 完成兄弟节点没有被重跑。

**成功证据：**

- CodeAgent WorkItem ID；
- ChangeSet commit；
- Wave merge commit；
- runtime PID/日志；
- `/health` 原始响应；
- `replay-l1-*.json`。

这一步验证“合同能否驱动代码交付”，不声称业务功能已经完成。

### Phase 3：Replay L1b 真实业务垂直切片

**范围：** 使用完整任务管理架构和合同，只选一条跨层业务链：

```text
POST /tasks
→ TaskService
→ TaskRepository
→ SQLite
→ GET /tasks
```

附带 `/health`，但不先做完整 Browser E2E。

**必须证明：**

- canonical Python module 和 symbol binding 正确；
- API method/path 与 provider/consumer 一致；
- service/repository 分层没有越界；
- 创建、查询、空白标题 400；
- 进程重启后数据仍存在；
- AST import gate 和 HTTP contract gate 都通过。

**成功证据：**

```text
replay-l1-business-*.json
import-binding-gate-*.json
http-contract-gate-*.json
runtime-business-smoke-*.json
```

这是判断“原始设计缺陷”和“ProjectOS 重构缺陷”的主要分界点。

### Phase 4：失败注入和恢复验证

在 L1b 通过后，逐项注入，不与完整 Live L2 混跑：

| 注入故障 | 必须证明 | 禁止发生 |
|---|---|---|
| Provider 403 | 立即 block，等待决策 | 消耗普通 retry |
| CodeAgent 502/503 | 只重试当前 WorkItem | 重跑成功兄弟节点 |
| terminal event 缺失 | 不伪造完成 | 产生 completed |
| CodeAgent 无 ChangeSet | 保持 incomplete 并局部重试 | 直接完成 |
| import binding 错误 | 定位 owner file/unit | 重跑全图 |
| HTTP contract 错误 | 定位 provider/consumer | 进入 Runtime |
| Wave 冲突 | freeze 当前 wave | 后续 wave 消费坏 baseline |
| Runtime 启动失败 | 进入 runtime repair/block | 用静态测试宣布成功 |

**成功证据：** failure package、recovery entrypoint、repair scope、frontier diff。

### Phase 5：Live L0 和 Live L1

只有 Phase 1–4 通过后才调用真实 Wanfa。

#### Live L0

- Provider preflight；
- Requirement → Architecture → Contract；
- 持久化 digest 和 checkpoint；
- 失败不得进入 Code。

#### Live L1a

只运行一个 CodeAgent 单元，验证真实 Provider 调用、ChangeSet、Wave 和 `/health`。

#### Live L1b

再运行任务管理业务垂直切片，验证 import/API/SQLite 的真实交付。

**Live 阶段规则：**

- 每个 WorkItem 单独观察；
- Provider 失败只报告当前 WorkItem；
- 不创建新 Trace 冒充原 Trace 恢复；
- 原 Trace 的历史失败事件保持不变。

### Phase 6：Replay L2 完整交付

Live L1b 通过后，先完整 Replay：

```text
所有 Code Waves
→ Tests sandbox
→ Review
→ Runtime
→ API E2E
→ Browser E2E
```

**L2 成功标准：**

- 所有 Code ChangeSet 和 Wave merge；
- sandbox Tests evidence；
- Review conclusion；
- Runtime startup/health；
- CRUD、筛选、完成/取消完成、删除、统计；
- Browser 加载、创建、交互、错误反馈；
- Replay Trace `completed`。

### Phase 7：原 Live Trace 局部恢复和完整交付

不直接从头重跑。按证据重建 frontier：

1. 读取事件、checkpoint 和 durable artifacts；
2. 确认已完成节点不重跑；
3. 只恢复当前失败的 CodeAgent/Integration owner；
4. 通过 L1b 后再进入 Tests、Review、Runtime、API、Browser；
5. 每完成一个下游阶段立即汇报；
6. 最终只有原 Trace `completed` 且证据索引完整时，才宣布交付。

## 4. 交付门和量化指标

### L0 门

- Architecture 分层 checkpoint 全部存在；
- Contract digest、ownership、binding、compiled plan 存在；
- semantic loop passed。

### L1 门

- 至少一个真实 CodeAgent ChangeSet；
- Wave merge commit；
- `/health=200`；
- 至少一个真实跨层业务 API 通过；
- SQLite 重启持久化通过。

### L2 门

- Tests、Review、Runtime、API E2E、Browser E2E 全部有原始证据；
- Trace status=`completed`；
- 历史失败 Trace 保留；
- evidence index 能从结论反查到 WorkItem、artifact、commit、命令和原始响应。

## 5. 运行反馈协议

每个阶段只允许出现三种结果：

1. `通过`：附证据，进入下一阶段；
2. `失败但可归因`：停止，提出修复选项；
3. `Provider/环境阻塞`：停止，不把外部故障写成设计成功或失败。

没有阶段性证据时，不使用“基本完成”“应该没问题”“继续等待”作为结论。

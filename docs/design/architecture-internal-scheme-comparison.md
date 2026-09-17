# Architecture 内部设计方案对照实验

> 状态：实验协议草案，2026-09-17。不是结果报告；在 B/C/D 尚未实现并运行前不得宣称方案优劣。

> 2026-09-17 更新：用户已要求四分支同期横评；下文先 A/B/C 再 D 的旧执行顺序已被替代。新准入和比较边界见 `architecture-fourway-protocol.md`；C/D 尚未实现，不能启动横评。

## 目标

先比较 A/B/C，再以综合最优方案与 D 横评。所有比较必须使用同一份冻结 Requirement、同一 wanfa 配置、同一工具集合和同一故障注入点。

## 方案定义

### A：当前基线
固定 `Blueprint → Module Design → Implementation Design → Integration → Quality Gate`。模块数量和依赖由 Blueprint 动态决定；每个设计节点一次提交完整正式产物；失败按现有 WorkItem 恢复或重规划。

### B：固定三层 + 节点内可恢复中间产物
层级不变。Module/Implementation 内增加有版本、输入摘要、阶段合同和确定性校验的中间结果；失败后从最近有效阶段继续，最终仍提交正式设计产物。中间结果不能绕过正式发布。

### C：固定三层 + 受控问题上报/父层修订
层级不变。子节点发现当前约束无法满足时提交结构化修订请求（需求引用、冲突约束、证据、建议），不得直接改父层。父层生成新版本后，控制面按依赖使受影响下游失效并重算。

### D：递归动态分层
设计节点按“是否达到可实现粒度”递归拆分；节点可产生子设计节点或实现边界。需要额外定义终止条件、父子版本、局部集成、并发 ownership 和恢复规则。D 不应在 A/B/C 结果前提前实现。

## 第一阶段 A/B/C 控制变量

- Requirement 使用一份冻结文件和 SHA256；禁止各方案重新生成需求。
- Provider 固定为 `wanfa`，模型、超时、工具 allowlist 和重试上限固定。
- 业务目标固定为任务管理项目：创建、完成/取消、筛选、删除、统计、SQLite 持久化、空标题 400、API/浏览器边界。
- Blueprint 由各方案独立生成，但输入和验收标准相同；不得把某方案生成的 Blueprint 传给另一方案，除非实验明确测试“同一上游产物下的内部执行差异”。
- 每个方案至少分自然运行组和故障注入组；故障注入必须记录位置和注入次数。

## 指标

1. 完成率、首次完成率；
2. Agent/WorkItem/Planner 三种重试分别计数；
3. LLM、工具调用数和总耗时；
4. 失败后重新执行的节点数和产物数；
5. 最晚发现问题的阶段；
6. 无效产物复用次数；
7. 需求覆盖、接口覆盖和正式发布证据；
8. 故障注入后是否能恢复到同一有效候选；
9. 机制新增代码、状态和测试维护成本。

综合评分不能只按完成率。建议先按硬门槛筛选：不得错误复用失效产物、不得越权修改父层、不得产生无正式证据的 completed；再比较恢复成本和复杂度。

## 故障矩阵

- Provider 在第一次生成中返回失败；
- 响应中断且未提交产物；
- 中间工具调用成功但最终提交失败；
- 父层产物在子层执行前后发生版本变化；
- 子节点发现接口/约束无法满足；
- 集成阶段发现跨模块接口冲突；
- 发布在 revision、current pointer、根投影之间中断。

## 决策规则

A/B/C 只在相同输入和相同故障矩阵下比较。若 C 的修订请求触发次数不同，不把“更早发现”误判为失败；应同时记录返工范围。若没有足够重复次数，不报告统计显著的成功率差异，只报告观察结果和置信边界。

第一阶段综合最优解必须写明：适用场景、代价、未解决失败类型。然后冻结该解，与 D 使用同样的控制变量再次横评。

## 当前状态

当前仓库已具备 A 基线、产物版本、依赖屏障、发布证据和部分恢复测试；B、C、D 尚未作为可比实验实现。历史 wanfa 运行不是 A/B/C 对照数据，不能填入比较表。下一步应先实现最小 B 或 C 的垂直切片，并为每次运行保存机器可读结果和 `evolution.md` 时间线。

## 2026-09-17 输入基座实测

最小节点对照先使用同一真实 Module 工作项及其全部上游输入，不让各方案独立生成 Blueprint 引入混杂。冻结包位于 `/Users/coinloner/projectOS/project/architecture-comparison-input-20260917-02`，摘要 `3d93a4841ab8c21603efa704694d4ac914aec0c48afb886352683795648e4728`。由只读发布审计通过的真实 wanfa 实例导出；不是新的 E2E 成功。

包内保存四个模块的合同及其上游输入并集，运行单个模块时只能提供该模块声明的输入，禁止将自身历史输出作为答案输入。Requirement 额外保存供追溯；如要加入模型上下文，必须对所有方案统一开启并明确区别于原始 A。

C 的 issue report 是检出终态，不是成功交付。没有父层修订后重新产出正式 Module 的证据，不得计算为恢复成功，也不得仅凭停止更早、调用更少判定优于 A/B。

## 已实现的配置边界（2026-09-17）

通过 `ArchitectureExecutionConfig(mode="baseline" | "checkpointed")` 传入 GraphRunner 的 `architecture_config` 参数。默认 baseline，不再使用 `architecture_checkpoints` 布尔参数。配置定义位于 `app/architecture_execution_config.py`，以避免可信 ExecutionContext 导入领域服务。

- profile 统一派生 checkpoint 工具增量与恢复提示，不能独立配置成矛盾组合。
- 实验执行器按 A/B 映射 profile，报告保存 `architecture_config` 和 `architecture_config_digest`；Trace 每次 Architecture 尝试也保存配置。
- checkpoint 与配置摘要绑定；产物验收合同及外部运行配置不随 profile 改变。
- 重建方式：`ArchitectureExecutionConfig(**saved_config)`。目前没有 Trace 自动恢复入口；调用方须显式注入，不应声称完整跨进程恢复已完成。
- profile SHA256 不包含源码、提示词正文和依赖版本；正式实验仍需额外冻结并记录这些公共条件。


### 15 对完成审计及成本对照

新增十对的 20 个 evidence 均确认 inputs_unchanged=true、同一 bundle digest、wanfa Provider；全部实际命中预定故障次数，16 个 completed 均 receipt=passed。

|组|场景|A 状态 / 秒 / WorkItem retry|B 状态 / 秒 / WorkItem retry|
|---|---|---|---|
|6|pre-input-read|completed / 84.50 / 1|completed / 97.18 / 2|
|7|pre-input-read|blocked / 65.56 / 0|failed / 79.41 / 1|
|8|post-input-read|failed / 369.11 / 1|completed / 106.59 / 1|
|9|post-input-read|completed / 154.17 / 1|completed / 96.62 / 2|
|10|pre-formal-commit|completed / 50.69 / 0|completed / 96.21 / 2|
|11|pre-formal-commit|completed / 58.84 / 1|failed / 183.15 / 2|
|12|pre-formal-commit-twice|completed / 83.13 / 1|completed / 305.19 / 2|
|13|pre-formal-commit-twice|completed / 118.46 / 0|completed / 231.66 / 2|
|14|post-formal-commit|completed / 80.79 / 0|completed / 136.21 / 1|
|15|post-formal-commit|completed / 97.39 / 1|completed / 150.79 / 1|

新增故障运行完成率：A 8/10，B 8/10。自然运行 5 对仍为 A/B 各 3/5；组合样本 A/B 各 11/15，仅为这套人工场景组合的描述性统计，不是生产故障分布下的成功率估计。

新增十次单臂运行累计耗时：A 1162.64 秒，B 1483.00 秒；累计 WorkItem retry：A 6，B 16。它们包含首次执行及非注入错误，不等于纯恢复成本，且不能推算 token 或货币成本。A 部分运行注入两次而 WorkItem retry=0，说明同一 Agent 内也可能重新调用工具；进一步证明不能把故障数、工具重试数和 WorkItem retry 混算。

B 在十次中九次观测到非空 checkpoint 加载（第10组只加载空记录）；但第11组即使加载 interfaces，最终仍失败。因此“checkpoint 可读”不等于“恢复完成”，当前证据不能支持默认切换 B。未量化语义阶段重做和 token 成本，也未验证跨进程崩溃恢复。

纠正执行中的措辞：输入读取故障确实是两臂共同工具边界，且所有实验均命中，不能称为“不公平命中”或“不是共同边界”。它不专门测量 checkpoint 收益，属于读取/工具故障恢复对照。不同后续模型行为本身是实验结果，不是自动排除理由。

历史样本审计额外发现一条未配对 A 运行：wanfa-matched-a-20260917-174747-644882（failed）。保留原始证据，不纳入15个匹配对，也不得隐去后用匹配完成率宣称所有已启动实验的总体完成率。匹配自然组1/2为191835与193735两个时间批次，组3/4/5为194856、205633、205938。

结论：已完成请求的15组（30次匹配运行），未证明 checkpoint 提高成功率或降低成本。暂不扩大生产重构；优先调查 B 正式提交恢复失败及额外重试来源。此轮故障为工具调用异常，不覆盖输入版本变更、响应流截断或 Worker kill；不以未执行场景作成功证据。

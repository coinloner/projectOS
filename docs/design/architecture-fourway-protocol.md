# Architecture 四分支横评：实现准入及实验协议

2026-09-17。状态：隔离基线已建立；C/D 尚未完成，禁止启动四方案横评。

本次用户要求替代旧协议中“先比较 A/B/C，胜者再与 D 比较”的执行顺序。
四方案完成后同期重跑，历史 A/B 15 对仅作为历史记录，不拼入新样本。

## 分支与配置隔离

共同快照：`22a3f4b330d08f6d1e7b5fe03a51c1b7fb64c5c7`。
快照包含源工作目录原有 tracked 修改及经检查的 untracked 源文件；未包含 .env。
通过独立 Git index 和 commit-tree 生成，原 index、HEAD、工作文件未被重置。

|方案|分支|独立工作目录|执行差异|
|---|---|---|---|
|A|codex/architecture-fourway-a-20260917|/Users/coinloner/.codex/worktrees/architecture-fourway-20260917/a|现有固定层级基线|
|B|codex/architecture-fourway-b-20260917|/Users/coinloner/.codex/worktrees/architecture-fourway-20260917/b|固定层级 + 内部 checkpoint|
|C|codex/architecture-fourway-c-20260917|/Users/coinloner/.codex/worktrees/architecture-fourway-20260917/c|固定层级 + 受控父层修订，待实现|
|D|codex/architecture-fourway-d-20260917|/Users/coinloner/.codex/worktrees/architecture-fourway-20260917/d|动态递归拆分 + 子层回收集成，待实现|

每个分支有独立 architecture-experiment.json；不能将另一 arm 的参数传入运行。
未实现的 C/D 不映射为 baseline；即使人为修改 profile 状态，也不开放 A/B-only 执行器。
共享 ProjectOS Provider、模型和外部配置保持不动；各实例仍新建在
`/Users/coinloner/projectOS/project/<name>`，不共享产物、Trace、恢复状态和工作目录。

## 完整实现的准入标准（不是已通过结果）

C 必须经过真实路径：子节点上报带需求引用的冲突 → 控制面授权父节点处理 →
父节点正式提交新版本 → 受影响后继输入重绑定/旧结果失效 → 子节点重新交付正式产物。
仅写 issue-report、停在 needs_replan、在脚本里改父产物，均不算闭环。
须验证：无关后继不重跑；子层不能越权改父层；修订次数有上限；中断恢复不重复修订；
输入改变后不能复用旧版本结果；无法解决的约束不能标 completed。

D 必须由模型按业务边界作终止/继续拆分决定，而非将固定 0/1/2 层改名。
至少一条真实路径有不同于固定三层的结构，子层正式完成后父层基于这些结果完成集成。
须验证：深度/节点/调用预算耗尽不是完成；父子版本、ownership、依赖可验证；
中断恢复不会重新执行已确认有效的分支；变更会使受影响子树及集成结果失效。
递归设计最终须适配共同正式交付合同，不能只交一个任务树。

两者都必须有确定性负向测试、正常路径测试、真实 wanfa 准入运行及正式产物证据。
模型产生的结果必须由实际工具校验、写入，不能用手写 fixture 替代真实运行证据。
准入运行与正式横评分开记录；不得用一次 smoke 成功证明稳定性。

## 保留 A/B 对比方式，不偷换比较单位

1. 继续使用已冻结真实上游 bundle：
   `/Users/coinloner/projectOS/project/architecture-comparison-input-20260917-02`。
   digest：`3d93a4841ab8c21603efa704694d4ac914aec0c48afb886352683795648e4728`。
2. 保留原 module 对照组的相同输入、相同工具故障注入、同一正式 receipt 成功门槛。
   C/D 必须确实调用各自实现，不允许退回 A 再标为 C/D。
3. 此单 module 组本身不足以测量 C/D 的设计能力。扩展组必须四臂一起使用相同完整
   Requirement/Blueprint 初始快照、同一业务任务、同一最终交付边界。
   若授权需求上下文，四臂同时增加，不能只给 C/D。
4. C 合法修订产生新的版本，不能将“当前父产物不同”直接判为输入污染。
   共同初始快照保持不变；修订链、授权和全部返工成本必须计入。
5. 旧故障注入针对 write_module_design；若 D 使用不同提交工具，必须显式建立共同
   正式交付边界的对应关系，先证实命中，再汇总。不能用未命中算恢复成功。
6. 共同总调用/时间/重试预算须在首个横评运行前冻结；C 修订与 D 递归不得获免费额外预算。
   保留正常多轮调用、工具级重做、Provider 内部重试、WorkItem 重试、父层返工的区别。
7. “一起横评”是四个同期匹配 arm，不要求同时冲击 Provider；若并发执行，固定并发度
   并记录启动时间/429/5xx。顺序运行则轮换 arm 顺序，不能总把同一方案置于服务低谷。
8. 记录每个启动运行，包括初始化失败、超时、取消和未命中故障；保留原始日志。
   比较完成率、首次完成率、耗时、可观察调用数、重复工作范围。
   没有 token/计费证据时不宣称货币成本或纯重试成本。

## 当前尚缺什么

- C/D 控制面和工具路径实现及上述准入证据。
- 四臂共同扩展组的执行适配及故障边界对齐。
- 正式四臂调度/聚合运行。当前只有只读预检，不是可运行横评系统。

预检故意失败关闭；不以分支存在、profile 声明、通过 A/B 单测作为 C/D 就绪证据。

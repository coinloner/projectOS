# Architecture 执行可靠性演进记录

## 2026-09-16：交付协议与完成证据回归核验（未完成全部重构）

范围：当前独立工作树。未修改主目录已有 evolution.md，未清理或覆盖主目录 dirty/untracked 文件。

### 已实施
- 为 design/baseline/api/data/frontend 分区注册 Markdown 交付协议，完成验证不再将 Markdown 当作结构化 JSON 解析；拒绝空白产物。
- Compact/Parallel 的集成显式使用 architecture_markdown_integration 和 create_architecture_candidate；结构化集成仍使用 integrate_architecture_designs。
- Markdown 集成即使 Agent 返回 completed，也必须有属于当前 work item 的唯一候选；缺失时阻断，不调用结构化集成兜底。
- architecture_only 增加明确集成节点，质量门消费集成候选而非分区 Blueprint。
- 撤回尚无真实输入版本证据的 CONTRACT_MISSING 熔断扩展。静态 contract_digest 不足以证明产物未变化。
- 修复新候选验证失败分支传入 FailureSignal 不支持的 expected_tool 参数问题，改用现有 retry_hint。

### 可复现验证
解释器：/Users/coinloner/projectOS/.venv/bin/python。
- 使用 git archive HEAD 导出到临时目录，独立运行原始基线测试，不修改任一工作树。
- 原始基线：587 passed，11 failed，5 subtests passed。日志 /tmp/projectos-baseline-tests.txt。
- 本轮新增测试前的改动树：587 passed，11 failed，5 subtests passed。失败测试名称与基线一致。日志 /tmp/projectos-current-tests.txt。
- 补充缺候选测试后 GraphRunner + 新模板测试：44 passed。该测试同时断言不调用结构化兜底。
- 新增注册表测试覆盖五类 Markdown 分区、空白拒绝、协议分流、未知 slot 和质量门候选来源。

### 尚未证明或完成
- 动态递归展开后的完整 barrier 验证仍未完成；基线中的两个 dynamic_builder 测试仍失败，不能视作可忽略。
- 缺失/失效上游产物的生产者恢复路由、真实输入版本判定与无效消费者重试抑制尚未完成。
- 本次没有真实 wanfa 新实例，未测得成功率和重试成本改善，不是新的 E2E 成功证据。
- 单元测试只证明限定协议与控制分支行为，不证明语义架构质量或 Provider 可用性。

最终本轮全量结果：592 passed，11 failed，10 subtests passed（17.77 秒）；失败集合仍与原始基线一致。日志 /tmp/projectos-final-tests.txt。测试集增加了五个测试方法，不能将通过数增加解读为既有缺陷全部修复。

## 2026-09-16：动态屏障与不可重试输入故障

- 两个基线 dynamic_builder 测试此前因未注册必需工具而在预检处终止。修正 fixture，注册真实 Architecture 工具后，两项实际动态运行测试通过。
- 增加动态模块和实现节点的完成事件先于集成启动的断言；增加子节点虚报完成但无产物的负例，确认后续实现和集成都未执行。动态测试 11 passed。
- Architecture 声明输入预检位于 Agent 创建和 durable-output 恢复之前。缺失或清单校验损坏的输入返回 PLANNER_VALIDATION，保留 ref_id 和生产者，明确要求修复生产者或引用，不在消费者上重试。
- FailureSignal 新增可持久化 retryable（默认 True），RetryPolicy 对显式 False 返回 BLOCK。不是按错误文本增加 regex 分支。
- 测试确认缺失输入不会创建 Agent；信号序列化后保留不可重试属性。此前定向测试 56 passed，后续另新增动态子节点负例。
- 限制：此阶段明确暴露计划/上游修复，不等于自动生产者恢复已实现；当前预检验证存在性及仓库清单，不替代跨产物语义合同校验，也未解决恢复后旧候选版本失效的全部问题。

本阶段全量测试：597 passed，9 failed，10 subtests passed（16.69 秒），日志 /tmp/projectos-round2-tests.txt。剩余九项均来自上轮基线失败集合；动态两项现已通过。全量仍未全绿，未宣称三个高优先级修改全部完成。

## 2026-09-16：候选输入版本与确定性集成失败

- ArtifactCandidate 保存各 source_ref 的创建时内容摘要；加载时保留该证据。缺少完整版本证据的旧候选不被恢复。
- candidate_for_work_item 排除输入已改变的历史候选，保留历史文件，不删除/覆盖；同一当前输入若有多个候选仍拒绝歧义。
- promote_candidate 再次校验上游摘要，避免绕过 Runner 直接发布旧候选。
- Runner 在完成校验、候选恢复及质量门处传入当前计划预期输入集合；质量门要求其候选生产者确实为当前计划的 INTEGRATION 节点。
- 测试使用仓库正常写接口生成和重写产物：旧候选不能发布、不会写正式 architecture.md；新候选可在重建 Repository 后选中并发布。另验证输入集合变化也拒绝恢复。
- 结构化集成兜底执行失败后标记不可在消费者处重试，返回修复上游/计划信号；已有无效结构化设计测试断言仅创建一个 Agent。没有新增错误文本匹配特例。
- 定向仓库、GraphRunner、动态展开测试：59 passed。
- 检查 18110 端口无监听；本轮未启动新服务，历史 pid 文件不能作为运行证据。
- 尚需完成：完整协议边校验与语义修复路线审查、真实 wanfa 节点闭环。版本检查不能防止所有跨进程并发写入竞态，不声称实现完整事务快照。

本阶段最终全量：598 passed，9 failed，10 subtests passed（16.63 秒），日志 /tmp/projectos-round3-final.txt；失败集合仍为之前相同九项。git diff --check 通过。

## 2026-09-16：计划协议边与分区恢复输入版本

### 计划级协议边核验
- ExecutionPlan 构造时检查同 Trace staged 输入的生产者存在、传递依赖屏障、slot/artifact_key 及生产消费协议一致性。
- QUALITY_GATE 必须指向相同发布目标的 INTEGRATION 节点。历史 Trace 输入仍允许显式引用，由执行前输入预检验证存在性。
- 专项负例覆盖协议混用、缺失生产者、缺失依赖、质量门直接消费分区。
- 全量：603 passed，9 failed，10 subtests passed，16.63 秒；日志 `/tmp/projectos-edge-final-tests.txt`。

### 新发现并修复的恢复缺口
- 原有分区恢复检查输出摘要、身份和 schema，但未检查产出所依据的输入版本；因此上游修改后仍可能复用旧输出。集成候选版本检查不能单独解决此问题。
- staged manifest 增加 source_refs/source_digests/contract_digest；Architecture 的 Markdown 和结构化写入均从可信 ExecutionContext 传入这些元数据，不由模型填写。
- Runner 正常完成验证与 durable recovery 均比较当前声明输入集合、输入内容摘要、执行合同摘要。缺少版本证据的历史产物保留，但不恢复。
- 不将未知输入证据等价为“已确认无输入”。回归测试覆盖仓库重建后验证、上游改变、输入集合改变、合同改变、重写后重新验证，以及 Runner 实际恢复分支。
- 全量（新增 Runner 回归测试之前）：605 passed，9 failed，10 subtests passed，16.84 秒；日志 `/tmp/projectos-staged-full-tests.txt`。
- 增加 Runner 回归测试后定向：51 passed，日志 `/tmp/projectos-recovery-proof-tests.txt`。未将这个定向结果表述为全量已绿。

### 剩余九项基线失败归类
- API 工作流清单与 Planner 默认模板预期（3 项）：测试仍预期旧 project_delivery 路径；当前注册/默认选择为 delivery_default 等新模板。
- Architecture 工具集合（3 项）：实际额外暴露 select_tech_stack/select_interface_kind，测试精确集合未包含。仍应审查这些工具是否确有必要，而不是仅修改预期使测试通过。
- Contract 依赖（1 项）：测试要求直接依赖 blueprint，当前为 integration 屏障。
- 模板编译 fixture（1 项）：注册 integration_agent，实际模板引用 code_integration_agent。
- Trace 事件（1 项）：测试预期未包含 tool_contract_preflight。
- 这九项证明测试与执行协议存在漂移，不证明真实 wanfa 历史失败都由这些因素导致。本轮未修改这些断言以追求全绿。

### 明确未完成的边界
- 输入摘要在产物写入时读取，不是执行开始时冻结的不可变快照；执行期间输入变化仍需处理。
- RLock 只保护同一个 Repository 实例，不提供跨实例/跨进程事务一致性。
- 已完成 checkpoint 的下游失效传播、候选执行合同变更、普通工具异常路径的消费者重试归属仍需审计。
- 未启动真实 wanfa 新实例，无匹配输入的成功率/重试成本数据；三个高优先级修改仍不能宣布全部完成。

## 2026-09-16：checkpoint 失效传播、外层重试与默认发布门

- Runner 恢复 checkpoint 时重新验证已完成 Architecture 分区/集成的持久证据；失效时清除受影响节点和依赖后代的调度完成状态，保留无关结果，返回显式计划错误。不自动复用过期的动态拓扑，也不声称已实现自动重建。
- RunCoordinator 遵守 `retryable=False`：不再对已阻断的失败启动 Planner 修复循环。回归验证一次 Runner 调用、零次修复 Planner 调用。
- 上述修改后全量结果：608 passed、9 failed、10 subtests passed，16.66 秒；日志 `/tmp/projectos-checkpoint-coordinator-full.txt`。九项为已记录的基线失败。
- 进一步发现默认 delivery_default 缺少 Architecture 质量门：集成仅创建候选，合同却直接依赖集成。补入与 architecture_only 相同的候选质量门，并让合同依赖该门。更新合同测试为完整 blueprint → integration → quality gate → contract 链的断言，不是单纯放宽旧断言。
- 发布门修改后定向测试：27 passed、5 subtests passed（模板、动态展开和协议边），1.82 秒；日志 `/tmp/projectos-default-gate-tests.txt`。`git diff --check` 通过。该修改后的全量结果另行记录。
- 移除 Responses 请求路径打印 API key 前缀及请求头的诊断代码；保留 model 的 debug 记录。正在运行的验证进程已加载旧代码，其原始日志需按敏感文件处理，不复制到文档。

### 新鲜 wanfa 实例（尚在运行，不计作成功）
- 项目：`/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260916-182002-5480ae`
- Trace：`tr-6fadf5722993`；Plan：`run-d4100996187b`。
- 正式容器、architecture_only 模板与 Runner；真实 Requirement 输出进入递归 Architecture，无手动生成/修改业务产物。脚本为 `scripts/validate_wanfa_architecture.py`。
- 18:25（Asia/Shanghai）观察：Requirement、Blueprint、四个 ModuleDesign 和 Schema Registry 的 ImplementationDesign 已完成，正在持久化 ImplementationDesign。统计 17 次 LLM 调用、21 次工具调用；不能将其视为重试次数。
- 验证只覆盖 Architecture，不包含 Code/Runtime E2E，也不是匹配输入的多方案成功率实验。该进程不包含启动后新增的默认模板发布门及日志清理改动。

### 仍需验证/修复
- 普通 ToolExecutionError 路径尚未准确区分模型参数错误与不可由消费者修复的上游语义错误；不能把所有集成异常一概设为不可重试。
- 输入身份仍在写入时取摘要，不是执行开始时冻结；候选执行合同 pinning 与跨进程提交一致性尚未闭合。
- 等待真实实例集成/质量门最终结果，之后再决定最小后续修复；不以当前通过的单测或中间节点成功宣布三项整改全部完成。

默认发布门修改后全量：609 passed、8 failed、10 subtests passed（16.15 秒），日志 `/tmp/projectos-default-gate-full.txt`。减少的一项是现已按显式发布屏障验证的 Contract 依赖测试，其余八项与基线记录一致；全量仍非全绿。

## 2026-09-16：真实 wanfa 失败定位与相同输入复验

### 原始结果（不改写失败记录）
- `tr-6fadf5722993` 在 18:28:28（北京时间）终止为 `needs_replan`，耗时 506.06 秒。10 个工作项完成，最终未发布架构。
- Worker 统计：23 次 LLM 调用、33 次工具调用；Trace metrics 为 2 次 `work_item_retrying`，并非 23 次重试。需求 SHA256：`ed5ff548950a7ea8f02f3a7d0d2064725e5938cc7585f8268ed151a9f2775523`。
- 集成报错：`实现设计 task-web-ui 引用了未声明的接口: task-api.http-api`。此次终止不是 Provider 错误。

### 确认的集成器缺陷，纠正初步归因
- 初步只看错误时将其描述为产物合同不一致；进一步检查原始提供方/消费方对象及转换代码后，发现集成规范化逻辑本身有问题，不能直接归因于模型产物。
- `_normalize_interface_references` 将提供方 ID 按模块目录规范化，却用规范化前的全局 provider_ids 解析消费方；因此消费方可能被反向改成已不存在的旧 ID。
- 修复为先构造最终提供方目录，再解析消费方。未增加 regex、别名规则或语义猜测。回归覆盖提供方和消费方同时有别名、两种输入顺序。
- 相同真实输入只读对照：修复前 validator 拒绝（0.0042 秒）；修复后 validator 通过，含 4 个 ImplementationDesign（0.0060 秒）。两次均零 LLM 调用，9 个输入摘要完全一致。证据：`/tmp/projectos-real-input-preflight.json`、`/tmp/projectos-real-input-catalogue-fix.json`。
- 该对照验证确定性校验缺陷及修复，不是成功率统计。未修改任何真实生成产物、未恢复 Trace、未发布候选，原运行仍为失败。

### 消费者预检
- 抽出 `validate_design_inputs`，与正式集成/合同编译复用同一设计集合校验；Runner 在创建结构化集成 Agent 及恢复候选前先执行。
- 无效上游输入返回不可重试的 `PLANNER_VALIDATION`，修复提示指向生产者/输入引用，不消耗消费者模型预算。模型参数校验原有受限重试仍保留。
- 测试改用合法完整设计对象验证确定性 fallback，不再以 `staged` 假字符串绕过真实校验。缺失/无效上游测试断言零 Agent 创建和显式计划错误。
- 预检修改定向：103 passed。目录修复后 Architecture schema + Runner：75 passed、2 subtests passed。全量待最后一轮结果。

目录修复后全量：610 passed、8 failed、12 subtests passed（16.46 秒）；日志 `/tmp/projectos-catalogue-full.txt`。失败集合未新增，`git diff --check` 通过。三项整改仍保持未完成：执行输入冻结/候选合同版本及新鲜真实集成发布验证尚需完成。

### 2026-09-16 — 候选恢复绑定执行合同

- 为 ArtifactCandidate 增加持久化 contract_digest；Architecture 从受信 ExecutionContext 写入，恢复时同时匹配输入引用集合与执行合同。
- Runner 的 Markdown/结构化完成检查、确定性回退检查、候选恢复和质量门选择均传入合同摘要；质量门使用集成生产者合同，而非质量门自身合同。
- 保留旧候选历史，但在指定当前合同时拒绝缺失合同或合同不匹配的候选。未指定合同的其他领域调用暂不改变行为。
- 新增重载仓库后的回归测试：同输入下旧合同及无合同候选不能满足新合同；创建新合同候选后唯一选中该候选。
- 定向验证：84 passed，2 subtests passed；git diff --check 通过。日志：/tmp/projectos-candidate-contract-tests.txt。
- 此结果仅证明候选选择的合同边界，不是新的 wanfa 实例成功证据。仍需处理执行开始到产物写入之间的输入一致性，并完成新实例 Architecture 发布验收。

### 2026-09-16 — 执行期输入摘要守卫（尚未完成并发一致性审计）

- Runner 在 Architecture 输入预检时捕获摘要并通过受信 ExecutionContext 传入工具；暂存及候选写入核对当前输入，持久化执行前摘要而非重新标记为写入时版本。
- Architecture 输入读取、结构化集成预检及候选复用增加版本检查。
- 新增输入在执行期间改变后，暂存与候选写入均被拒绝且不产生正式产物的回归测试。
- 定向测试 85 passed、2 subtests passed；git diff --check 通过。日志 /tmp/projectos-input-versions-tests.txt。
- 明确保留边界：这不是跨进程原子快照；检查与读取间存在并发窗口，需要继续审计工具读路径与中途失败的控制面重试归类。尚未启动新的真实 wanfa 验证，三个修改整体未宣告完成。

### 2026-09-16 — 中途输入失效停止重试；新 wanfa 实例启动

- 增加 load_versioned_ref，对实际返回内容计算摘要，避免仅检查后再次无条件读取最新内容。
- Runner 在 Agent 正常返回和异常出口均重新检查执行前输入版本；失效返回 retryable=False 的计划错误，要求修复生产者或引用。
- 回归覆盖正常返回及抛出异常两条路径：各只调用 Agent 一次，均停止于 needs_replan。最初测试因未注册工具被预检拦截，补齐真实工具注册后测试通过，未绕过预检。
- 定向组合验证 107 passed / 2 subtests；新增中途变化测试后 GraphRunner 45 passed / 2 subtests。git diff --check 通过。
- 新真实 wanfa 项目：/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260916-184824-5e004b；Trace tr-683860c4d196；Plan run-89869a6afc4c。
- 进程会话 10442 已确认仍运行；Requirement 已完成，Architecture Blueprint 已开始。日志 /tmp/projectos-wanfa-contract-snapshot-live.log。未手动生成或修补产物，未恢复旧 Trace。结果待观察，不宣称成功。

### 2026-09-16 — 新真实实例终态及归因核对

- 会话 10442 已退出；validation-evidence.json 记录 needs_replan，耗时 471.374 秒。12 个工作项完成（Requirement、Blueprint、5 ModuleDesign、5 ImplementationDesign），集成预检拒绝，质量门未发布。
- 拒绝原因：Blueprint.required_files 要求 schemas.json；task-schema-registry 的 ImplementationDesign 却声明 schemas/schemas.json，根路径无人 ownership。
- 已核对原始 Blueprint、ModuleDesign、ImplementationDesign 和最终 Plan：ImplementationDesign 节点获授 Blueprint 与 ModuleDesign 引用，缺失的不是 Blueprint 输入引用。ModuleDesign 未分配文件路径，ImplementationDesign 自行选择了不符合 Blueprint 必需路径的目录。
- 本次不是上次接口归一化错误，也没有证据指向 Provider 故障。属于父子设计约束不一致，被全局合同校验正确发现。不能据此认定所有约束都已在 prompt 中充分呈现，仍需进一步审计。
- 第三项修改的真实证据：集成 signal.retryable=false；retry_count=0；12 次 tool_contract_preflight 均发生于前序节点，集成在 Agent 创建前拒绝；不是反复调用消费者修复相同上游。
- 该结果证明失败被及时、明确拦截，不证明 Architecture 发布成功，更不证明 Code/Runtime 全链路成功。未修改实例产物，未重启此 Trace。
- 全量回归会话 9652 正在运行，结果尚未确认。

### 2026-09-16 — 全量回归及未知协议拒绝

- 全量会话 9652 已结束：613 passed、8 failed、14 subtests passed（57.15 秒）。失败项与此前记录相同；日志 /tmp/projectos-final-boundaries-full.txt。不是全量绿灯。
- 审计发现 contract_for 对未知 Architecture 分区返回 None，而原 Runner 可以跳过产物合同完成校验。新增 ArchitectureProtocolPreflight：PARTITIONED/INTEGRATION 缺失注册合同，在创建 Agent 前返回不可重试计划错误。
- 回归用例明确断言未知分区不创建 Agent、不接受纯自然语言完成。协议、Runner、动态展开组合：66 passed、7 subtests passed；日志 /tmp/projectos-protocol-preflight-tests.txt。
- 父层约束传递审计：TaskInputPackage 的 InputBinding 明确只携带引用、不含正文；DynamicPlanBuilder 的实现节点包含 Blueprint 引用，但 acceptance_criteria 和 delivery_contract.architecture 目前未显式投影 Blueprint.required_files。不能据此断定模型未读取蓝图，也不能自动把全局 required_files 分配给每个模块（会制造重复 ownership）。需继续基于调用证据判断是否为提示丢失或语义违约，不进行 schemas.json 路径特例修复。

### 2026-09-16 — 继续核对失败共同因素：已读取父层，仍缺少可执行的约束分配

本节补充并修正上节归因，不修改真实实例产物，也不恢复失败 Trace。

#### 本轮重新核实的直接证据

- 最新实例 `wanfa-architecture-protocol-20260916-184824-5e004b`，Trace `tr-683860c4d196` 的 `.projectos/runs/tr-683860c4d196/memory.jsonl`：sequence=57，tool_name=`load_architecture_input`，返回完整 Blueprint，包括 `required_files=["schemas.json"]` 和 schema 层 `path_mapping=["schemas/**"]`。sequence=59 再读取 ModuleDesign；sequence=60 调用 `write_implementation_design` 成功。
- 因此本实例不是“未授予 Blueprint 引用”或“模型未读取 Blueprint”。不能据此建议增加记忆层来解决这次失败。
- `app/agent/architecture_agent.py` 的通用提示同时规定：Blueprint 不声明具体文件，具体文件路径属于 depth=2；包含数据模型时必须生成 Schema Registry (schemas.json)。此外通用工作流程仍将集成工具写成 create_architecture_candidate，而结构化协议规定 integrate_architecture_designs。任务专用合同可能消解部分歧义，但通用提示本身没有按协议完全分流。
- `ArchitectureBlueprint.required_files` 接受具体文件路径，却没有结构化字段把每个必需文件分配给某个 module；`LayerDecision.path_mapping` 声明目录组织边界。现有 Bundle 检查必需文件 ownership，但该检查不能代替层目录覆盖/归属检查。
- `ArchitectureService.write_staged_design` 的当前路径检查单体 DTO、depth、预算及大小后写入。它不在此处加载父层并执行跨层语义一致性校验。输入摘要记录证明结果基于哪些输入，不等于结果满足这些输入的约束。

#### 归因边界

- 根目录 schemas.json 与 schemas/** 是提示意图下的明显张力；尚不能称为数学上无解：若 path_mapping 只是建议，根目录文件仍可实现；若是强制边界，就应有明确的根文件归属/例外规则。当前主要缺陷是该语义未统一，而非已证实所有父层合同都不可满足。
- 上一实例是接口归一化实现错误；最新实例是跨层路径承诺不一致。它们不是同一个直接错误，也不能与历史 Provider server_error/upstream_error 混成一种根因。
- 可以支持的共同设计风险：多个位置表达合同（通用提示、任务专用合同、DTO、归一化、Bundle 校验），语义不完全一致；局部提交成功不代表父子约束已成立；大量成本投入后才在汇总边界暴露错误。
- 最新实例集成零重试说明“停止无效消费者重试”起效，但不是整体成功率提高的证据。现有两次真实运行不是同版本、同冻结输入的随机对照，不能据此计算方案间成功率。

#### 下一步取舍建议（未实施，非已验证结论）

1. 先统一合同语义，再决定是否细拆节点或补记忆。不增加 Agent，不做 schemas.json 路径特例。
2. 通用提示按已注册协议给出正确终结工具；不把某个固定文件名强加给所有架构。
3. 明确 required_files 的定位：用户/外部强制路径必须保留并指派唯一责任；一般设计选择可由负责模块决定。不能把全局必需路径复制给所有模块。
4. 已有父层输入足以判定的约束在生产者提交前检查；跨兄弟节点的唯一 ownership、接口覆盖等保留全局检查。不能要求每个子节点独自满足全局 required_files。
5. 验证分开：冻结真实输入的离线负例验证规则；故障注入验证恢复/停止行为；新的 wanfa 实例验证自然产出与正式发布。旧输入通过校验不替代新实例闭环。

本轮仅做只读证据审计和本记录追加；未启动新 wanfa 调用、未修改生成产物、未更改业务实现、未运行新的测试。已有全量结果仍为 613 passed / 8 failed / 14 subtests；不得视为本轮重新验证。

### 2026-09-16 — 统一通用 Architecture 提示；启动新实例验证

- 修改 app/agent/architecture_agent.py：按 Markdown/结构化交付协议分别列出提交工具，结构化集成使用 integrate_architecture_designs；去除对所有数据模型架构强制独立 Schema Registry 和固定文件名的要求。
- 明确 required_files 是精确路径承诺而非文件名建议；一般文件布局下放实现层，外部硬约束继续保留，要求责任模块遵守且冲突时报告。此处仅修正提示，不声称已实现结构化责任分配或跨层提交前语义校验。
- 定向测试：66 passed / 7 subtests passed（2.40 秒）；日志 /tmp/projectos-prompt-protocol-tests.txt；git diff --check 通过。测试验证控制分支回归，不证明提示能稳定产生正确设计。
- 启动全新 wanfa Requirement → Architecture → integration → gate 验证，项目 /Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260916-205841-3315a9；Trace tr-e626bdfb314f；Plan run-74ce2764275f。未手动生成需求或修补输出。
- 持久 PTY 会话 65175；日志 /tmp/projectos-wanfa-unified-prompt-live.log。启动时状态 running，尚无终态结论；本实例不包含 Code/Runtime E2E。

### 2026-09-16 — 质量门拒绝也明确停止无效重试

- 审计发现 _run_quality_gate 捕获无效候选后仅返回普通 failed，未携带不可重试语义。现对缺失候选、失效输入、合同不匹配等捕获的发布拒绝返回 needs_replan + ArchitectureQualityGate / retryable=False，要求修复生产者或引用，不把发布当作可修复输入的消费者。
- 新增回归覆盖缺失候选、旧合同候选、输入变化三条路径，断言不可重试且 architecture.md 不出现；原成功质量门发布测试保留。
- GraphRunner、RunCoordinator、RetryPolicy 组合：69 passed / 5 subtests passed（4.48 秒），日志 /tmp/projectos-gate-nonretryable-tests.txt。
- wanfa 会话 65175 本轮再次轮询确认仍运行。该进程启动时已加载旧 Runner，本次质量门失败分支修改不纳入此实例的运行版本证据；正常发布分支未变。终态仍待验证。

### 2026-09-16 — 发布与 checkpoint 边界的故障注入验证

- 上一轮属于有进展：修改质量门非重试失败路径并验证负例；本轮首先确认 wanfa 会话 65175 仍存活，未重启或重复提交。
- 重新执行全量（发布恢复补丁之前）：615 passed / 8 failed / 17 subtests，46.84 秒。失败集合与此前八项相同，日志 /tmp/projectos-current-full-tests.txt；仍不是全量通过。
- 审计发现完成 checkpoint 校验仅覆盖 Architecture 分区和集成，未检查 quality gate 的正式发布证据。现恢复时核对当前候选身份、合同、输入版本、发布 revision 内容摘要以及根目录投影；缺失/失效发布不能复用 completed 状态。
- ArtifactRepository.promote_candidate 对 current.json 已指向同一候选的重放复用原 revision，并重建根目录投影，避免发布后 checkpoint 丢失导致新增版本。损坏的正式 revision 拒绝而不静默改写。正常质量门返回 completed 前也显式核验发布证据。
- 故障注入：在 current.json 写入之后、投影保存时抛出 OSError；重新创建 Repository 并重放同一候选，验证只保留一个 revision、投影恢复且发布证据有效。另验证仅有 gate completed 状态而没有正式发布不能恢复，发布后可以恢复，删除投影后再次拒绝。
- 首次新增测试错把缺失投影的异常预期为 FileNotFoundError；WorkspaceStore.load 对不存在文件返回占位文本，摘要校验正确抛 RuntimeError。修正测试预期，没有放松校验。
- 组合测试：101 passed / 10 subtests，4.73 秒，日志 /tmp/projectos-publication-boundary-tests.txt；最后增加的正常质量门发布后核验需随下一轮测试确认。
- 边界：上述恢复没有实现跨进程事务或任意位置崩溃的 exactly-once；revision 写入后、current.json 之前中断仍可能留下孤立 revision。真实 wanfa 在这些补丁之前已启动，不能作为新增恢复分支的实测证据。

### 2026-09-16 — 全新 wanfa Architecture 闭环完成与独立发布核验

- 项目：/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260916-205841-3315a9；Trace tr-e626bdfb314f；Plan run-74ce2764275f；validation-evidence.json 记录 completed，耗时 452.874 秒，12 节点完成，工作项 retry_count=0。实际运行包括 Requirement、Blueprint、4 Module、4 Implementation、Integration、Quality Gate。需求由实例真实生成，未手工补写生成产物。
- 候选 cand-d939b40cf966 引用 9 份架构源产物，发布 published:architecture:rev-001；摘要 0f0ce17c3feb06b69a82a33c3f39de723d3077492f77736a06afb85fe1729583。需求摘要 f9f1a716bbc3ace57c6fd39c32007d51c4807c0e6d26cc75d7365a7336486296。
- 使用最新代码只读核对候选身份、合同、源版本、正式 revision 和根目录投影；核对动态子节点全部完成后才开始集成、集成完成后才开始 gate；恢复 checkpoint 验证通过。审计输出 /tmp/projectos-wanfa-publication-audit.json 和 /tmp/projectos-wanfa-harness-audit.json。首次独立审计误传 checkpoint 外层包装，改为读取 state 后通过；未修改 checkpoint 或生成数据。
- 验证脚本新增可复用只读 audit_architecture_publication，后续 completed 运行自动保存 publication_audit；非 completed 终态退出码为 1。该成功实例是在脚本增强前运行，增强后的核验为事后只读审计。
- 发布后核验补丁定向测试 59 passed / 5 subtests；随后全量 617 passed / 8 failed / 17 subtests（23.91 秒），日志 /tmp/projectos-final-publication-full.txt。八项失败仍为 API/Bootstrap/Planner 受控模板约定、delivery_default_integration Agent fixture、工具集合、Trace 事件期望；没有为了全绿修改这些测试期望。
- 证据边界：真实运行在新增发布恢复/质量门失败补丁加载前已启动；不能声称真实 Provider 触发过新增故障恢复分支。恢复能力证据来自故障注入和事后验证。单次自然运行成功不是稳定成功率测量；不包含 Code/Runtime E2E，也不能修复此前无效的横向对照实验结论。

### 2026-09-16 — 收紧未知集成协议的默认匹配

- DeliveryContractRegistry 原先会将显式未知 stage_id 因 publish_target=architecture 或历史 work_item_id 后缀而识别成结构化集成。现在仅 stage_id 为 None 时保留默认匹配；显式未知阶段（包括空字符串）不再继承该合同。
- 新增注册表负例及 Runner 在模型创建前非重试拒绝的测试，覆盖 partitioned 和 integration。首次参数化测试错误地给两种执行模式同时携带 slot 和 publish_target，被既有授权校验拒绝；修正测试构造为各模式合法授权，没有放松产品校验。
- 本补丁不会宣称已实现 required_files 的结构化责任分配或完整父子语义检查；这是另一个设计取舍，不继续扩展此次三项修复范围。
- 最终补丁后回归：102 passed / 14 subtests（4.73 秒），日志 /tmp/projectos-final-protocol-guard-tests.txt；全量 618 passed / 8 failed / 21 subtests（52.51 秒），日志 /tmp/projectos-final-scope-full.txt；失败名称集合未变化，git diff --check 通过。
- 此次三项修复范围已具备代码与定向测试证据，并取得一次全新 wanfa Architecture 正式发布证据；不以此声明全量测试通过、长期稳定性得到统计验证或完整产品交付成功。

### 2026-09-17 — 开始实施 A/B/C 对照实验基础

- 当前 A 仍是既有固定三层 Architecture 基线。为避免继续停留在方案描述，ArtifactRepository 新增节点内部中间产物的最小版本化存取接口：`write_intermediate` / `load_intermediate`。中间结果保存阶段名、输入摘要、内容摘要和时间，不构成正式交付，且输入或内容摘要不匹配时拒绝恢复。
- 该接口目前是 B 的存储垂直切片，尚未接入 Architecture Agent 的完整阶段执行流程，因此不能把 B 视为已经完成或把该代码当作对照结果。
- A/B/C/D 的真实比较仍待：将 B 接入一个 Module Design 节点、实现 C 的结构化问题报告、固定同一 Requirement 和故障矩阵后运行。历史实例继续不作为横向数据。

### 2026-09-17 — 冻结真实模块对照输入，尚无方案胜出结论

- 新增 scripts/freeze_architecture_comparison.py：先只读验证真实实例的发布及依赖屏障，再按真实 Module WorkItem 的 input_refs 收集前置产物，保存任务合同和摘要；输入正文或任务合同变化均拒绝校验。重复目标目录拒绝创建，未覆盖历史实例。
- 实际冻结源：wanfa-architecture-protocol-20260916-205841-3315a9 / tr-e626bdfb314f。有效实验输入目录：/Users/coinloner/projectOS/project/architecture-comparison-input-20260917-02；bundle digest：3d93a4841ab8c21603efa704694d4ac914aec0c48afb886352683795648e4728。
- 五份真实输入：Requirement、Blueprint、三个被其他 Module 依赖的 Module 产物；四份真实 Module 工作项合同。用于每个 Module 单独的同输入对照，不是将冻结 Module 答案提供给它自身。所有方案必须遵循同一输入投影规则，不能把所有冻结文件无差别交给模型。
- 核查发现当前 Module input_refs 未直接引用 Requirement；冻结包额外保存 Requirement 供实验来源追溯，不擅自改变真实工作项输入合同。第一版 -01 未保存 Requirement，保留但不用于正式实验，由 -02 取代。
- 新增 tests/test_architecture_comparison_bundle.py，3 passed；git diff --check 通过。该测试只证明冻结包内容/任务的完整性检查，不证明 B 的恢复或任何模型成功率。
- 实验边界纠正：C 仅提交 issue 并停止不能算闭环恢复成功；必须分别记录冲突检出、父层修订和最终正式产物完成。A/B/C 尚未运行对照，B 执行接入、C 修订闭环仍待完成；不提前选优或开始 D 横评。

### 2026-09-17 — 对照基础回归核验

- 在当前 dirty worktree 上重新运行对照基础定向回归：`tests/test_artifact_repository.py tests/test_architecture_comparison_bundle.py tests/test_architecture_protocol_registry.py`，结果 `28 passed, 7 subtests passed`。
- 该结果只证明仓储版本校验、冻结输入完整性和协议注册表的现有基础没有回归；不等价于 A/B/C 运行结果，也不证明 B 已接入 Agent 或 C 已完成父层修订。

## 2026-09-17 — Fresh A baseline run (wanfa)

A new, non-restored Architecture-only run was executed with `wanfa` in:
`/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260917-040504-ae295d`

- Trace: `tr-34c78b9a4daf`
- Plan: `run-04c2fab581e2`
- Provider: `wanfa`, model `gpt-5.6-terra`
- Elapsed: 448.903s
- WorkItems completed: 10
- WorkItems failed: 0
- Retry count: 1
- Terminal status: `needs_capability`

The run reached requirement, blueprint, module design, and implementation design. It did **not** complete Architecture publication: integration stopped with `needs_capability` because the required `integrate_architecture_designs` capability was not available in the effective tool list. This is evidence of a current control/tool-contract gap, not evidence that A completed successfully. It must not be counted as a completed A sample, but should be retained as a failed/blocked trial when analyzing retry cost and failure location.

### 2026-09-17 — Fresh A baseline run (wanfa, 2026-09-17 04:46)

- Project: `/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260917-044611-a9737c`
- Trace: `tr-f211706a7957`
- Plan: `run-1a2ab2b98aab`
- Provider: `wanfa`, model `gpt-5.6-terra`
- Scope: fresh requirement -> Architecture only; no Code/Runtime.
- Result: **not completed**; `wi-03-architecture-integration` ended `needs_capability` because the runner/provider-visible tool set did not expose `integrate_architecture_designs`.
- Metrics: elapsed `355.349s`; `work_items_completed=8`; `work_items_failed=0`; `retry_count=1`; `event_count=80`.
- Observed control-plane issue: Blueprint attempted `load_architecture_input(requirement)` and received `tool_authorization` because the current WorkItem was not authorized for that reference; the workflow nevertheless continued and produced Blueprint and downstream designs. This is an input-contract/authorization defect and must be isolated before counting A/B/C success rates.
- This sample is retained as evidence of a fresh A baseline failure/block, not counted as successful completion and not used as B/C evidence.

### 2026-09-17 — Integration fallback 根因纠正及回归修复

- 对上一条记录作纠正：模型 capability_request 中的“工具未提供”不是工具实际缺失的证据。Runner 在真实 Trace tr-f211706a7957 中记录了 architecture_integration_control_plane_fallback；代码检查发现 fallback 成功只更新 agent_result，而返回的 node_result 已提前转换，仍保留 needs_capability。不能再将该终态简单归因为工具暴露失败。
- 新增 test_integration_capability_fallback_updates_node_result，真实执行本地 Blueprint → Module → Implementation → Integration；Agent 在集成返回模拟 capability_request。修改前复现 blocked（没有可提供能力来源）；原有同类 fixture 使用 _base_plan(False)，根本未执行 Integration，不能覆盖此问题。
- 修复 Runner：fallback 创建候选后按当前 source_refs/contract_digest 验证候选，更新返回的 NodeResult；不绕过产物合同、不添加 Agent、不改真实实例产物。
- 定向回归：tests/test_dynamic_builder.py、tests/test_graph_runner.py、tests/test_architecture_protocol_registry.py：70 passed / 14 subtests passed；git diff --check 通过。
- 这是控制面故障的确定性复现与修复，不是新 wanfa 成功样本，也不是 ABC 对比结果。Requirement 授权归因仍待精确核对：本轮直接看到的是 report_progress 的 confirmed_refs 被拒绝，不能把它未经核对地记为 load_architecture_input 失败。

### 2026-09-17 — 对照前置：隔离恢复提示词并验证输入绑定

- 当前轮实际执行而非沿用历史测试：初始 domain_services/artifact_repository 回归 25 passed。
- 新增 tests/test_architecture_intermediate_versions.py：13 个用例覆盖缺失/错误版本证据、执行中输入变化、相同字节但不同引用、合同和 slot 变化，以及独立服务重新加载同版本 checkpoint。已验证中间产物绑定的是引用身份、内容版本、合同及 slot，而不只是摘要列表。
- BaseAgent 增加按执行上下文取策略文本的窄入口；Architecture 不再对所有任务无条件要求中间工具。只有分区上下文显式授权两个中间工具时才加入恢复规则，避免 A 基线被 B 指令污染。不扩大任何工具权限，不改变迭代预算。
- 新增 tests/test_architecture_prompt_isolation.py，覆盖 A/B/A 顺序不串扰、单工具授权不足以及非分区上下文不启用规则。
- 本轮联合回归：prompt_isolation、intermediate_versions、crewai_agent_adapter、domain_services、dynamic_builder，共 47 passed，1 个已有 collection warning。
- 证据边界：这是 B 存储恢复安全性与提示词隔离的测试，不是完整 Runner 重试恢复，更不是 wanfa A/B/C 成功率实验。B 的控制面工具授权仍未接入；C 在工具/服务/Runner 中尚无父层修订闭环；D 未启动。版本不匹配当前明确报错，尚不等于自动失效重算。

### 2026-09-17 — B 首个真实 Runner 重试闭环（确定性测试，非 Provider 实验）

- GraphRunner 新增默认关闭的 architecture_checkpoints 配置，仅对 Module/Implementation 分区增加两个中间工具授权；Blueprint/Integration 保持原合同。对照开关不改变正式交付校验和重试预算。目前为 Runner 构造配置，未宣称跨进程恢复配置已持久化。
- tests/test_architecture_checkpoint_runner.py 使用相同蓝图、相同模块设计与同一语义故障点：第一次边界阶段完成后抛 connection reset，经过 Runner 正常重试、Gateway 工具调用和正式 Module 校验。A/B 均两次模块尝试后 completed；边界执行次数 A=2、B=1，接口阶段各一次。
- 这证明基础重试路径可以读取持久 checkpoint，而不是只有存储单元测试。测试 Agent 是确定性的，不是 wanfa；不能报告真实成功率或 token 节约。
- 初版测试错误地将 design 字段展开传给 write_module_design，出现 schema 拒绝；依据真实工具 schema 改为 design 包装后通过，未放宽生产校验。
- 联合回归 checkpoint_runner / graph_runner / dynamic_builder / prompt_isolation / intermediate_versions：76 passed、7 subtests passed、2 warnings。
- 扩展 domain_tools 回归有两项失败，工具清单断言缺少四个已注册工具。临时移除且完整恢复本轮 Runner 开关后单独复测仍为 2 failed / 9 passed，证实不是本轮开关引入；不据此声称全套测试通过。
- 下一步仍需同冻结输入的 wanfa 对照执行器和 C 修订闭环，才能做 ABC 选型；D 依旧未开始。

### 2026-09-17 — Architecture A/B 配置边界落地

- 新增不可变 ArchitectureExecutionConfig，当前仅支持 baseline / checkpointed 两个已实现 profile，未知模式和版本直接拒绝。提示词、工具增量与中间恢复由同一 profile 派生，避免独立布尔值互相矛盾；没有虚设 C/D 开关。
- GraphRunner 的 architecture_config 替代旧 architecture_checkpoints 实验参数，通过可信 ExecutionContext 注入。仅 Architecture Module/Implementation 可获得 checkpoint 工具增量；非 Architecture 节点保持默认策略。Provider、Planner、全局重试、Agent 迭代预算和正式产物合同不变。
- ArchitectureAgent 需配置和工具授权同时满足才追加恢复提示，不修改共享 Agent 状态；checkpoint 输入签名加入配置摘要，切换方案不得复用旧中间结果。
- 每次 Architecture 执行写入 architecture_execution_config Trace 事件；匹配实验报告保存配置及 SHA256，可通过配置字典重建 profile。摘要仅标识配置，不替代源码/提示词内容的版本指纹。
- 联合回归 86 passed / 7 subtests passed，两个既有弃用警告；测试覆盖配置不可变/序列化、A/B/A 提示隔离、适用节点限制、配置变更拒绝 checkpoint、真实 Runner 确定性重试与正式产物。首次扩大回归发现领域包 eager import 导致循环，配置移至 app/architecture_execution_config.py 后独立收集及联合回归通过。
- 范围限制：本轮没有运行新的 wanfa 对照，不代表成功率结论；未实现从 Trace 自动恢复 Runner 配置，重启调用方仍须显式传入保存的配置；未改 C 父层修订和 D 动态层级。现有旧 checkpoint 因绑定签名升级不能作为新实验恢复证据。

### 2026-09-17 — A/B 匹配 Pilot 1（wanfa，task-persistence）

- 使用冻结 bundle `architecture-comparison-input-20260917-02`，digest `3d93a4841ab8c21603efa704694d4ac914aec0c48afb886352683795648e4728`；A/B 均只导入同一授权 Blueprint 引用 `staged:tr-e626bdfb314f:wi-02-architecture-blueprint:blueprint`，输入 SHA256 `f9fb267e91373b42726166aa17f8ea1403a6db3e1081630590d2505cacd1faa0`，并确认 `inputs_unchanged=true`。
- A workspace `wanfa-matched-a-20260917-191835-b15f92`，Trace `tr-2bed0c0e477e`，baseline config digest `54f5424cf1d61d3da9a6f04ea393108d299ad6323784f7b8f5dd9fa755832b2f`。B workspace `wanfa-matched-b-20260917-191835-b71044`，Trace `tr-86b9617c30a9`，checkpointed config digest `8042caf51b6d7a08fda5ddabcf2a9d7a2e579f15f81358af9ca1d0050955d171`。
- A、B 均失败于 Agent runtime，wanfa 返回 `502 upstream_error`，均经过同样的 WorkItem retry（metrics: `retry_count=1`、`event_count=13`、`work_items_completed=0`），最终 retry quota exhausted。A 用时 673.14s，B 用时 429.09s；由于 Provider-level failure 未产生 Architecture 正式产物，不能将耗时差异解释为 A/B 机制差异。
- 该 Pilot 是匹配执行器和外部 Provider 故障分类的有效数据，但不是成功率或恢复成本的有效方案优劣样本；记录为 A/B 同时遇到 Provider outage，不计入完成率分母中的机制成功/失败结论。下一组需在 wanfa 可用时重跑，或使用确定性故障注入隔离恢复机制。


### 2026-09-17 — 匹配 Pilot 2 终态及统计口径纠正

- A: `wanfa-matched-a-20260917-193735-931be2`, Trace `tr-e6661681740a`, status=failed, elapsed=346.97s, inputs_unchanged=True; wanfa 502 upstream_error。
- B: `wanfa-matched-b-20260917-193735-07ae90`, Trace `tr-0c5945534749`, status=failed, elapsed=631.07s, inputs_unchanged=True; wanfa 502 upstream_error。
- 两个进程均已确认 exit 1 后才启动第三组，未复用产物。
- 纠正此前“上游错误不计入完成率分母”的表述：所有已启动实验都必须纳入端到端完成率；失败原因另行分层。Provider 错误样本不足以单独判定 Architecture 恢复机制优劣，不应被剔除来改善成功率。

### 2026-09-17 — A/B 匹配 Pilot 3：首次双成功

- A：workspace `wanfa-matched-a-20260917-194856-35ae20`，Trace `tr-c73525b1b113`，baseline；completed，正式 receipt validation_status=passed，产物 digest `c3e64ce00179a8b483e6a629ba357b821ca0584d7b4ff254f37ad64092980cc3`，22.93s，retry_count=0，inputs_unchanged=true。
- B：workspace `wanfa-matched-b-20260917-194856-23cb5d`，Trace `tr-7c66d0ed0254`，checkpointed；completed，正式 receipt validation_status=passed，产物 digest `abc7eb668724c0755f9c7c485ae09489bf8b47c30841cfc78979691fd2c96a71`，98.06s，retry_count=1，inputs_unchanged=true。
- 两者使用同一 frozen bundle、同一 Blueprint 输入引用和 SHA256、同一 Module contract；各自产物 digest 不同，均通过正式校验。B 在一次 WorkItem retry 后完成，但本次没有记录可比较的中间阶段执行次数，因此不能把 1 次 retry 或耗时差异归因于 checkpoint 机制。该组是有效的“双成功”可解释样本，但样本量仍不足以选择 A/B。


### 2026-09-17 — 匹配 Pilot 4 / 5 完成

两组组间串行、组内 A/B 并行，沿用同一冻结输入、task-persistence 和 wanfa 配置；四个新实例均 completed，receipt validation_status=passed，inputs_unchanged=true。

| 组 | 方案 | 秒 | WorkItem retry | Trace | 实例 |
|---|---|---:|---:|---|---|
| 4 | A | 46.76 | 1 | `tr-5fe3aeca664a` | `wanfa-matched-a-20260917-205633-4b7325` |
| 4 | B | 72.23 | 1 | `tr-0001a0b53b82` | `wanfa-matched-b-20260917-205633-f63dac` |
| 5 | A | 23.86 | 0 | `tr-183e3b735ead` | `wanfa-matched-a-20260917-205938-46318c` |
| 5 | B | 67.15 | 1 | `tr-4fe64bb5fd23` | `wanfa-matched-b-20260917-205938-c43c86` |

累计五组：A/B 各完成 3/5；前三个双成功组（3/4/5）A retry 为 0/1/0，B 为 1/1/1。这里只是观测结果，不能把自然运行耗时等同于失败后重算成本；尚需审计 B 重试原因及 checkpoint 实际复用证据，不宣称恢复收益。

### 2026-09-17 — 扩展至 15 对：实验专用工具故障矩阵启动

保留已有 1–5 组自然运行，新增 6–15 组：输入工具调用前失败、读取成功但返回前失败、正式提交前失败一次、正式提交前失败两次、正式提交成功但返回前失败，各两组。全部使用同一冻结 bundle 和真实 wanfa；组内独立进程/项目目录，组间串行。无生产配置或全局 retry policy 修改。

实现：scripts/architecture_comparison_faults.py 仅由实验脚本显式安装、限定 trace 的工具包装器；异常属于工具调用失败，不模拟 Provider 中断、Worker 崩溃或进程重启。记录注入次数、工具进出及 checkpoint 返回；未到达故障点必须标记 fully_exercised=false，不可称为恢复通过。scripts/run_architecture_fault_matrix.py 运行新增十对并保留每次失败。

测试：test_architecture_comparison_faults / checkpoint_runner / execution_config：13 passed。验证故障前后副作用次数、一次/两次故障预算、其它 trace 不受影响、包装器退出恢复，以及未命中不算 exercised。

矩阵位置：/Users/coinloner/projectOS/project/architecture-fault-matrix-20260917-212907-379ed6/matrix.json（进行中）。

实验局限：这些是两臂共有的工具边界，但模型到达边界和异常后采取的行动可能不同；不能由完成率直接归因 checkpoint。尤其输入读取阶段尚无可复用的设计 checkpoint，属于恢复负对照。输入版本变化及真实 Worker 崩溃不在本轮矩阵内。阶段重做量不能从工具调用次数或 llm_calls 推定。

### 2026-09-17 — 故障矩阵 6–15 完成（15 对总样本）

矩阵运行完成，位置：`/Users/coinloner/projectOS/project/architecture-fault-matrix-20260917-212907-379ed6/matrix.json`。新增 10 对、20 个独立项目目录；连同此前自然运行 1–5 对，累计 15 对、30 个 Architecture module 实例。所有新增实例均真实调用 wanfa，使用冻结 bundle `architecture-comparison-input-20260917-02`，未恢复旧 Trace、未手改成功产物。

新增场景分组：6–7 `pre-input-read`，8–9 `post-input-read`，10–11 `pre-formal-commit`，12–13 `pre-formal-commit-twice`，14–15 `post-formal-commit`。每个项目的 `comparison-evidence.json` 保存 arm、group_id、fault_scenario、config digest、输入完整性、receipt、metrics 和 fault_evidence；矩阵 manifest 保存每一对的实例证据路径。

结果不能直接解释为 B 优于 A：`pre-input-read` 并不是 B checkpoint 可恢复边界，且两臂模型可能在故障后走不同工具路径；它暴露了实验边界而不是方案效果。`post-input-read` 的到达/提交行为也有模型工具选择混杂。较可比的正式提交前/后故障仍应以实际 fault_evidence、checkpoint load、重复阶段和 receipt 分层分析，不能用 llm_calls 或总 retry_count 推断阶段重做成本。


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


## 2026-09-17：四方案独立分支准备（不是横评结果）

用户要求 A/B/C/D 四个独立分支，先完成 C/D 基础，再按 A/B 方法同期横评。
已从当前含未提交修改的代码创建共同快照 `22a3f4b330d08f6d1e7b5fe03a51c1b7fb64c5c7`，
使用临时 index，不改变原 index/HEAD、不 reset、不 clean。四个分支及 worktree 见
`docs/design/architecture-fourway-protocol.md`；创建清单在
`/Users/coinloner/projectOS/project/architecture-fourway-setup-20260917-fourway/branches.json`。

新增实验分支身份检查，拒绝跨 arm 使用分支；运行 evidence 增加 source commit、branch、dirty 状态及 profile。
C/D 没有实现路径时硬拒绝运行，不能通过把配置标签改成 implemented 来冒充方案完成。
新增只读四分支预检；这不是四臂执行器。

重新确认：当前 issue report 只记录冲突，不会修订父产物；固定 depth=0/1/2 不能视作递归设计。
旧单 Module 用例继续保留，但不足以验证 C/D；扩展闭环组必须四臂同输入、同最终交付门槛、同总预算。
C/D 准入测试与正式横评分开；未命中故障不能算恢复成功。

本阶段未调用 wanfa、未新增模型实验样本、未产生四方案优劣结论。C/D 实现和真实准入运行仍未完成。

### 本阶段实际验证

分支隔离/输入校验/故障探针/既有 A/B checkpoint 相关定向测试：源工作目录以及四个独立 worktree 均各自执行，均为 `50 passed, 7 subtests passed`。
这些是共同基线与隔离检查，不是 C/D 功能测试，不证明 C/D 完整或四方案效果。
四分支预检 `ready_to_benchmark=false`，阻塞项为 C/D 实现未完成及真实 wanfa 准入证据缺失。
预检报告：`/Users/coinloner/projectOS/project/architecture-fourway-setup-20260917-fourway/preflight.json`。
## 2026-09-23 — Preserve the newest real Architecture execution baseline

The newest real instance is:

```text
project: /Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260923-003652-d81f6b
trace: tr-ac55c258291e
plan: run-981096391db0
workflow: architecture_only
provider/model: wanfa / gpt-5.6-terra
```

It was launched from the `eb2a` worktree at detached `HEAD 419d573`, together
with the then-current tracked working-tree changes. It completed Requirement,
Blueprint, five ModuleDesign nodes, and one ImplementationDesign node. It failed
at `wi-architecture-implementation-task-storage` after two provider attempts
returned `502 upstream_error: Upstream access forbidden`; Integration and the
quality gate were not reached. This is therefore a source-preservation baseline,
not a completed Architecture result.

The exact observed call chain, required source files, and merge regression rules
are recorded in:

```text
docs/design/latest-instance-preservation-20260923.md
```

All tracked changes in the worktree are preserved together on a dedicated Git
branch before any integration with `main`. Future merging must use Git history
and three-way comparison; it must not replace `main` by copying this worktree.


## 2026-09-23 Architecture D 迁移阶段 1：语义单元合同落地

在 `codex/migrate-architecture-d-to-main` 分支开始正式迁移，不把此前 D 分支的存在误认为完整实现。第一阶段保留 legacy/file 合同行为，同时为 D 合同增加显式 `compilation_strategy=semantic`：

- Blueprint 增加 `architecture_scheme` 与顶层模块 `boundary_role`（business_capability/user_experience/runtime）；D Blueprint 缺少边界角色时确定性拒绝。
- ImplementationDesign 的 `owned_files` 从“恰好 1 个”调整为 1-3 个具体文件，并保留路径、required_paths 和重复 ownership 校验。
- Project Contract 显式保存 `compilation_strategy`；D Blueprint 投影为 semantic，legacy 默认为 file。
- semantic implementation unit 编译为一个 Code WorkItem，不再被 `_split_file_units()` 按文件机械拆开；legacy/A/B/C 路径继续按文件拆分。
- WorkItem 与 Runner 的单文件限制仅对 legacy contract 生效；semantic unit 仍需具体 1-3 文件 ownership。
- Architecture Agent 提示同步到 capability-first 和 1-3 文件语义单元边界。

验证：

```text
119 passed, 6 warnings, 9 subtests passed
```

当前仍未宣称 D 完成：recursive split/leaf 执行、deterministic Contract、D tasks projection、节点级 receipt/recovery 和 wanfa 全新 E2E 仍需继续迁移。


## 2026-09-23 Architecture D 迁移阶段 2：生产方案显式化

在 ArchitectureExecutionConfig 中增加显式 `scheme`，默认值为 `D`，并提供 `recursive_enabled`。Architecture Agent 在生产上下文中收到 D 方案提示，要求输出 `architecture_scheme=D` 和顶层 `boundary_role`，避免仅凭实验文件或分支名称判断当前方案。

验证：

```text
69 passed, 1 warning, 2 subtests passed
```

这一步只完成了运行配置和提示边界的显式化；Contract 确定性执行、tasks projection、真正递归 split/leaf 计划和全新 wanfa E2E 尚未完成。


## 2026-09-23 Architecture D 迁移阶段 3：Contract 与 Tasks 的确定性执行入口

继续迁移 D 的控制面边界：

- D 的 `contract` WorkItem 在 GraphRunner 中直接调用已有 `ArchitectureArtifactWorkflow.compile_project_contract_from_designs`，从已接受的 Blueprint/ModuleDesign/ImplementationDesign 结构化输入编译 Project Contract，不再创建 Architecture Contract Agent 的模型调用。
- 新增 `app/domain/task/projection.py`，从 Project Contract 确定性生成 `tasks.md`。D 的 `tasks_plan`、`tasks_integration`、`tasks_quality_gate` 通过控制面执行投影和确认，不调用 TaskAgent；legacy 模板节点仍保持原行为。
- Project Contract 仍是 Code、Integration 和下游实现流程的权威事实，tasks.md 仅作为可读投影。
- 语义单元的 1-3 文件 ownership 会继续传递到 Code WorkItem 和真实 ChangeSet 校验。

验证：

```text
81 passed, 3 warnings, 7 subtests passed
```

剩余迁移项：D 的递归 split/leaf 计划执行、逐层 ValidationReceipt、节点内/跨节点恢复边界、默认流程的全新 wanfa E2E。

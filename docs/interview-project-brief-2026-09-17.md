# ProjectOS 面试讲解稿

> 基于 2026-09-17 当前代码与运行证据整理。目标不是把未完成部分包装成完成，而是准确说明：项目解决什么问题、为什么这样设计、当前做到哪里、卡点是什么、下一步如何闭环。

## 一句话定位

ProjectOS 是一个面向软件交付的半托管多 Agent 编排平台。它不追求让一个大模型拿到所有权限后“一次性生成项目”，而是把需求、架构、实现、测试和审查拆成可追踪的 WorkItem DAG，并用工具权限、版本化产物、Git ChangeSet、Docker Sandbox 和 Trace Evidence 约束模型行为。

## 最推荐的开场方式

不要说：

> 我做了一个可以全自动开发软件的多 Agent 平台。

应该说：

> 我在做的是一个 AI 软件交付控制面。模型负责产生候选方案和局部实现，控制面负责流程、权限、依赖、产物版本和完成证据。当前控制面与动态架构链路已经基本成立，但完整的代码到 Runtime 验收闭环还在收口阶段。

这句话同时表达了项目目标、技术边界和当前阶段。

---

# 一、3 分钟项目讲解

## 1. 背景与问题

现在用大模型生成代码并不难，真正困难的是把它放进一个可持续的软件工程流程：

- 模型可能跳过步骤或声称已经完成，但实际上没有落盘产物；
- 多个 Agent 并行时可能修改同一个文件，产生覆盖和冲突；
- 模型如果拥有 Shell、网络、Docker 等宽泛权限，安全边界不清楚；
- 某个节点失败后，如果全流程重跑，成本高且可能产生新的随机结果；
- 最终的“完成”不能只依据模型的一段自然语言，而要有可验证证据。

所以 ProjectOS 的核心问题不是“如何再封装一次 LLM 调用”，而是：

> 如何把不确定的模型能力嵌入一个确定、可审计、可恢复的软件交付控制面。

## 2. 核心流程

系统目前采用“固定生命周期 + 动态项目结构”的混合模型：

```text
Requirement
  -> Architecture Blueprint
  -> 动态 Module Design DAG
  -> 动态 Implementation Design DAG
  -> Architecture Integration Candidate
  -> Quality Gate / Publish
  -> Project Contract
  -> CodeAgent WorkItems / Waves
  -> Git Integration
  -> Environment / Test Sandbox
  -> Review
```

固定的是软件交付阶段以及阶段之间的合法转换；动态的是具体项目有多少模块、模块依赖、实现单元、文件所有权和并行 wave。

这样做是因为：完全固定的模板无法适应不同项目，而完全由模型生成 DAG 又容易越过流程与权限边界。

## 3. 核心架构

### Planner 与 ProcessDefinition

Planner 只生成受限草案，不能直接执行工具。控制面通过 ProcessDefinition、模板和校验器把它编译成可信的 `ExecutionPlan`。模型可以表达“做什么”，但不能自行声明 Docker 权限、文件写入范围或发布权限。

### GraphRunner 与 WorkItem DAG

GraphRunner 根据依赖关系调度 WorkItem，支持有界并发、wave 屏障、失败分类、有限重试、checkpoint 和 resume。每个 WorkItem 都有明确的 Agent、输入引用、输出类型、所需工具和执行模式。

### Agent 与 ToolGateway

Agent 不直接拿 Python 函数、Shell 或宿主机权限。ToolGateway 根据 domain、execution mode、allowlist 和 capability grant 决定当前节点可见的工具。也就是说，同一个 ArchitectureAgent 在分区、集成和质量门阶段能看到的工具不同。

### Artifact 与 Git ChangeSet

Markdown/结构化设计产物采用：

```text
staged -> candidate -> quality gate -> immutable revision -> current projection
```

代码采用独立 Git worktree 和 ChangeSet：分区 CodeAgent 只能写自己的路径，Integration 在控制面进行三方合并，正式 workspace 不由分区 Agent 直接覆盖。

### Sandbox 与 Evidence

测试不允许 Agent 自由拼 Shell 命令。Agent 只能请求固定的 check ID，控制面根据白名单 runtime profile 生成 Docker 执行规格。容器默认无网络、只读根文件系统、非 root、限制 CPU/内存/PID/超时，并把结果保存成结构化 SandboxEvidence。

### Trace、Memory 与恢复

每次运行有稳定 trace_id。计划、事件、工具调用、失败、产物引用、SandboxEvidence 和 checkpoint 都会持久化。Memory 用于上下文组装与决策召回，但事实权威仍然是正式产物、版本和执行证据，不能用聊天记忆替代。

## 4. 当前进度

截至 2026-09-17：

- 七个领域 Agent、受限 ToolSet、Planner、GraphRunner、FastAPI 控制面已经实现；
- WorkItem DAG、有界并发、Trace、checkpoint/resume、失败分类和有限重试已经实现；
- Architecture 已支持 Blueprint -> Module Design -> Implementation Design 的动态展开；
- Project Contract 可以进一步编译成文件级 CodeAgent WorkItem；
- 代码分区已有 Git worktree/ChangeSet/Integration 边界；
- Docker Sandbox、runtime profile、依赖审批和执行证据已经实现；
- 当前工作树全量测试为 627 passed、8 failed、21 subtests passed；Python compileall 与 git diff check 通过。

但我不会把它描述为“完整跑通”。最新真实模型运行已经走到 Architecture 内部的 Module 和 Implementation 层，但 Architecture Integration 因工具可见性和输入引用授权合同不一致进入 `needs_capability`，没有形成最终发布版本。完整的 Code -> Test -> Runtime -> Review 连续 E2E 还需要在这个集成边界修复后重新验证。

## 5. 为什么这个未完成状态仍然合理

这个项目的完成标准不是 API 返回 200，也不是 Agent 输出“done”，而是同时满足：

1. 正式产物已经持久化并带有版本；
2. 下游读取的输入版本与计划一致；
3. 代码变更满足文件 ownership 和 ChangeSet 约束；
4. Sandbox 产生真实执行证据；
5. Review 能读取原始证据并给出明确终态。

因此系统在工具合同不满足时停止，是当前缺陷的暴露，也是安全设计在发挥作用。错误的做法是绕过质量门、手工补文件或把模型文本当成完成证据。

## 6. 下一步

当前最短闭环路径是：

1. 修复 Architecture Integration 的 required_tools 注册/可见性以及 Blueprint input_refs 授权；
2. 同步默认 Workflow 更换后遗留的测试合同；
3. 用确定性 Fake/Stub Agent 完成完整 DAG 回归，排除模型随机性；
4. 用真实模型连续运行 Architecture -> Contract -> Code -> Runtime；
5. 覆盖至少前后端、数据库、异步流程和依赖审批四类实例；
6. 在 E2E 稳定后再删除兼容模板，并引入父层修订与局部失效重算。

---

# 二、30 秒版本

> ProjectOS 是一个 AI 软件交付控制面。它把需求、架构、代码、测试和审查编译成 WorkItem DAG，模型只负责受限节点，控制面负责依赖、权限、版本、Git 合并、Docker 测试和 Trace 证据。我的核心设计是用确定性系统约束非确定性模型：Agent 不能自行获得 Shell 或发布权限，产物必须经过 staged、integration 和 quality gate，代码必须通过 ChangeSet，测试必须产生 SandboxEvidence。当前控制面和动态架构三层已经跑起来，真实运行可以到 Implementation Design，但 Architecture Integration 还有工具可见性和输入授权合同问题，所以完整 E2E 尚未宣称完成。我现在的工作重点是收口这个边界并建立可重复的多项目验收矩阵。

---

# 三、8—10 分钟展开顺序

## 第一段：项目动机

强调你解决的是工程控制问题，而不是“多放几个 Agent”：

- LLM 擅长生成，不擅长长期状态和权限边界；
- 软件交付需要可追踪依赖、明确所有权、失败恢复和客观完成证据；
- 因此模型应处于数据面，控制面必须由确定性代码掌握。

## 第二段：一次请求如何执行

可以按下面顺序讲：

1. API/CLI 接收目标并创建项目；
2. Planner 获取 Agent、模板、artifact 状态和 runtime 摘要；
3. PlanValidator 将不可信草案编译成可信 ExecutionPlan；
4. GraphRunner 找出 ready WorkItem，按并发上限执行；
5. Runner 创建 ExecutionContext，绑定 trace、work item、执行模式和输入引用；
6. ToolGateway 只注入当前上下文允许的工具；
7. Agent 通过工具产生 staged artifact 或 Git ChangeSet；
8. Integration 和 Quality Gate 发布正式产物；
9. TestAgent 请求固定 Sandbox check；
10. Review 读取正式产物和原始证据，形成终态报告。

## 第三段：最重要的三个技术决策

### 决策一：固定阶段，动态模块

- 固定生命周期保证不会跳过 Requirement、Integration、Test 和 Review；
- Blueprint 决定模块和模块依赖；
- DynamicPlanBuilder 将模块图转换成 WorkItem DAG 和 wave；
- 这是安全性与适应性的折中。

### 决策二：完成由证据决定，不由 Agent 文本决定

- Agent 返回 completed 不够；
- 必须存在满足 DeliveryContract 的唯一产物；
- integration 必须产生 candidate；
- quality gate 才能 promote；
- test 必须有 SandboxEvidence；
- 这样能防止“自然语言虚报完成”。

### 决策三：最小权限与资源所有权

- 不给 Agent 任意 Shell；
- 工具按 domain、execution mode 和 allowlist 暴露；
- 文件按 WorkItem ownership 切分；
- 代码通过 task worktree 和三方合并进入正式 workspace；
- 冲突时阻止发布，而不是自动选择一方。

## 第四段：当前问题与反思

目前最有价值的教训是：多 Agent 系统真正困难的不是 Agent 数量，而是跨层合同一致性。

现在暴露的真实问题包括：

- Workflow 声明 required tool，但工具注册或执行模式没有同步；
- 节点知道某个 input ref 存在，但执行上下文没有授予读取权限；
- 默认模板升级后，API、Planner、测试 fixture 和 Trace 事件期望发生漂移；
- 上游版本变化后，不能让下游无条件复用旧结果。

因此当前重构重点是工具预检、输入摘要、contract digest、显式 integration barrier、issue report 和中间产物恢复，而不是继续增加 Agent 数量。

---

# 四、当前测试结果怎么讲

推荐原话：

> 我在 2026-09-17 对当前工作树执行了全量测试，结果是 627 passed、8 failed、21 个 subtests 通过，compileall 和 diff check 通过。8 个失败主要集中在默认 Workflow 重命名/替换后的旧断言、集成 Agent fixture 名称、工具可见性合同变化，以及新增 tool preflight 事件后 Trace 期望未同步。它说明主体没有系统性崩溃，但当前重构还没有达到绿色基线。除此之外，我还有一个真实 E2E 阻塞：Architecture Integration 所需工具未进入有效工具列表，以及一个 Blueprint 输入引用授权问题。这两个是真正要先修的运行问题，我不会用单元测试通过数掩盖它。

可将 8 个失败分为：

1. Workflow/API/Planner 旧模板期望尚未同步；
2. Delivery Default 测试仍使用旧 Integration Agent 标识；
3. Architecture Tool execution mode/contract 断言尚未同步；
4. Trace 新增 `tool_contract_preflight` 后旧事件序列断言未更新。

注意：不要说“只是测试坏了”。应说“其中多项是合同升级后的测试同步问题，但仍有真实 E2E 工具授权缺口”。

---

# 五、面试官高频追问

## 1. 为什么不用一个 Agent 全部完成？

> 单 Agent 的优势是链路短，但上下文、权限和失败范围都过大。需求、架构、代码、测试的工具需求不同；让一个 Agent 同时拥有全部权限，很难判断它为什么做出某个修改，也无法局部恢复。拆分后可以建立最小权限、清晰产物合同和并行 wave。代价是协调复杂度上升，所以我的重点不是 Agent 越多越好，而是只有在边界、产物和失败恢复都清晰时才拆分。

## 2. 为什么不用 LangGraph/CrewAI 自带流程直接做？

> CrewAI 在项目中主要负责单个 Agent 的 tool-calling loop，但软件交付需要额外的领域控制语义，例如 artifact revision、文件 ownership、Git ChangeSet、依赖审批、SandboxEvidence、checkpoint 校验和质量门。通用框架可以提供图执行，但这些“什么才算完成、谁能发布、输入版本是否仍有效”的规则仍需要自己的控制面。

## 3. Planner 会不会生成危险计划？

> Planner 输出被视为不可信草案。它只表达目标和逻辑依赖，不能直接声明执行模式、文件权限、Docker 命令或发布目标。PlanValidator、TemplateCompiler、ProcessDefinition 和 DeliveryContract 会补全或拒绝这些字段。真正的授权由控制面生成。

## 4. 如何防止模型说完成但没写文件？

> Runner 不以文本结果作为唯一完成条件。不同节点有 DeliveryContract：例如 Architecture 分区必须存在 staged artifact，Integration 必须存在唯一 candidate，Code 必须存在 ChangeSet 和 required paths，Test 必须有 SandboxEvidence。缺少证据时即使 Agent 返回 completed，节点也会被判定为失败或阻塞。

## 5. 多个 CodeAgent 如何避免互相覆盖？

> 每个实现单元有 allowed_paths、owned_files 和 wave，Agent 在独立 Git worktree 中提交 ChangeSet。Integration 检查共同 baseline、路径所有权和冲突，再进行受控三方合并。正式 workspace 只由控制面发布。

## 6. 如何处理失败？

> 先把失败分类成 Agent、WorkItem、Planner 或外部能力问题，再决定重试范围。输入格式错误应在节点内修正；产物缺失可重试 WorkItem；跨节点合同问题进入 replan；外部依赖或 capability 缺失则暂停等待审批。系统使用有限预算，不做无限重试。

## 7. Memory 在这里做什么？

> Memory 保存 Trace 内的事件、摘要、决策候选和可检索上下文，帮助后续节点减少重复推理。但它不是事实数据库。正式产物、输入引用、版本摘要和执行证据才是事实边界，否则旧记忆可能污染当前运行。

## 8. 你认为项目最难的部分是什么？

> 不是调用模型，而是跨层合同一致性：Workflow、WorkItem、ExecutionContext、ToolGateway、ArtifactRepository 和恢复机制必须对“节点能读什么、能写什么、什么叫完成”有完全一致的理解。最新真实运行正是在这个边界暴露了工具可见性和输入授权不一致，这也是我当前最优先修复的问题。

## 9. 为什么不直接自动回滚或修改父层架构？

> 子节点如果能直接修改父层，会破坏 ownership 和下游有效性判断。更合理的是子节点提交结构化 issue report，由父层生成新版本，控制面计算哪些下游产物失效并局部重算。当前仓库已经开始加入 issue report 和输入版本摘要，但完整父层修订闭环还没有完成。

## 10. 这个项目现在能用于生产吗？

> 不能。它现在是一个控制面和纵向链路原型，适合验证架构、权限和恢复机制。进入生产至少还需要稳定的多项目 E2E、跨进程任务队列和 worker lease、认证授权、真实 MCP connector、依赖与镜像治理、指标告警，以及更完整的恢复和数据迁移策略。

## 11. 为什么保留旧模板？

> 新的动态链路还未完成多项目迁移验证。直接删除旧模板会让历史 Trace 无法恢复，也失去回归基线。因此旧模板被降为 compatibility adapter；等动态链路完成阶段六、七验证后再删除。这是演进式迁移，不是一次性重写。

## 12. 如果只能再做一天，你先修什么？

> 第一优先修复 Architecture Integration 的 required_tools 注册与 execution mode 可见性，并修正 Blueprint input_refs 授权；第二，把新默认模板对应的 8 个测试失败归零；第三，用确定性 Agent 跑通完整 DAG。只有确定性闭环稳定后，才值得继续消耗真实模型调用成本。

---

# 六、容易说错的地方

## 不要夸大

- 不要说完整项目交付已经跑通；
- 不要说 627 个测试通过就代表 E2E 完成；
- 不要说 Memory 已解决跨运行智能学习；
- 不要说 MCP 已接入，当前真实 connector 尚未完成；
- 不要说多 Agent 一定优于单 Agent；
- 不要把一次真实运行到达 Implementation Design 说成最终发布成功。

## 推荐表达

- “控制面骨架和动态架构纵向链路已成立”；
- “完整 E2E 尚未达到可重复验收标准”；
- “当前阻塞点可定位到工具合同和输入授权边界”；
- “系统选择 fail closed，没有伪造通过产物”；
- “下一步有明确且可验证的闭环路径”。

---

# 七、白板结构

即使不展示，也可以按这个图口述：

```text
                    ┌────────────────────────────┐
User Goal ────────> │ API / Planner / Process    │
                    │ 不可信草案 -> 可信 Plan      │
                    └─────────────┬──────────────┘
                                  │ ExecutionPlan
                    ┌─────────────▼──────────────┐
                    │ GraphRunner / WorkItem DAG │
                    │ 并发、屏障、重试、恢复         │
                    └───────┬─────────┬──────────┘
                            │         │
                    ┌───────▼───┐ ┌───▼────────────┐
                    │ Agents    │ │ Trace/Checkpoint│
                    └───────┬───┘ └────────────────┘
                            │ trusted context
                    ┌───────▼─────────────────────┐
                    │ ToolGateway / Access Policy │
                    └───────┬──────────┬──────────┘
                            │          │
                    ┌───────▼─────┐ ┌──▼───────────┐
                    │ Artifact/Git│ │ Sandbox       │
                    │ versioning  │ │ Evidence      │
                    └─────────────┘ └───────────────┘
```

图的中心观点是：Agent 在控制面内部受限执行，而不是控制整个系统。

---

# 八、结尾总结

> 这个项目当前最重要的成果，不是已经生成了多少业务代码，而是建立了一套能够明确回答“谁在什么输入版本下，以什么权限，产生了什么产物，依据什么证据被判定完成”的控制模型。现在完整 E2E 还没有收口，但失败已经能够被定位为具体的工具合同和输入授权问题，而不是一个不可解释的模型黑盒。下一阶段就是把这个可解释控制面推进到可重复的真实交付闭环。

# ProjectOS 演化复盘

这份文档记录系统从“能生成一次结果”走向“可审计、可恢复、可监管的交付链”时暴露的问题。它与 API 文档不同，重点是为什么要改、改动影响了什么，以及当前仍然不能承诺什么。

## 1. 运行路径收敛

早期同时存在旧 CLI、受控模板、动态 Planner 和未注册的 `requirement_generation` 模板。入口和模板清单不一致时，调用方会得到“未注册 Workflow”，而不是清晰的能力错误。经过收敛后，运行时只有两条路径：

- 受控 Workflow：复用已经验证的依赖和权限边界；
- 动态 Planner：生成并校验一次 DAG，再交给同一个 GraphRunner 执行。

`requirement_agent` 仍是领域 Agent，但 `requirement_generation` 不再作为独立运行路径。这样保留了需求整理能力，同时避免第三条隐式编排路径改变主流程。

## 2. 计划稳定性与动态性

LLM Planner 在同一目标上可能生成不同节点数量，原因是自然语言规划没有稳定的结构约束，且模型会把“已有文件”误认为已经完成某个节点。模板能稳定演示，但会隐藏动态规划能力。当前折中方案是：Planner 只能引用注册 Agent、结构化 `WorkItem` 和依赖规则；局部修改使用 `PlanPatch`，只使受影响子图失效。稳定性来自边界和校验，灵活性仍来自动态 DAG，而不是依赖固定模板。

## 3. 端到端闭环暴露的共性问题

| 现象 | 根因 | 处理方式 | 当前边界 |
|---|---|---|---|
| 链路停在能力审批 | 有等待状态，却没有批准和恢复入口 | 增加 capabilities 查询、approve，并在同一 checkpoint 恢复 | 仍需要人工授权，这是设计要求 |
| 依赖项目确定性 BLOCKED | `dependencies_approved` 一直传 `False`，但没有依赖审批 API | 增加 runtime dependency approval 状态和 approve 接口 | 依赖下载仍受所有者授权 |
| Docker 镜像缺失才发现 | 受信镜像是隐藏前置条件 | preflight 和环境节点自动准备、缓存复用 | 真实 `docker pull` 仍依赖主机网络和 Docker daemon |
| 动态计划跳过需求/架构/环境 | Planner 只根据当前 manifest 和输入文件判断，默认 `runtime.yaml` 让环境触发条件失效 | 将产物和阶段约束写入 WorkItem 验证；质量门检查缺失产物 | 模型仍可能给出过短计划，需要评测集持续约束 |
| 复杂电商项目只生成领域骨架 | CodeAgent 在一次实现节点中优先完成 domain/model/schema/ports，未覆盖 architecture 要求的 interface/application/infrastructure/frontend；测试虽可针对骨架通过，但不代表系统可运行 | Review 对 AC-1~AC-11 和 Layer Contract 做确定性审查，缺层、缺入口、缺前端时判定 BLOCKED；后续应拆分实现任务或提高 CodeAgent 的分层完成门禁 | 当前可证明“质量门能拦截不完整交付”，尚不能证明单个 CodeAgent 能稳定完成大型全栈实现 |
| Review 未产出 | Worker/模型异常时 API 进程被第三方执行阻塞，或测试节点提前终止 | CrewAI 放入独立 spawn Worker；父进程补写终态并保留 checkpoint | 模型长时间无返回需要进度监管，已在本轮加入 |
| Worker 启动即失败且缺少 `plan.json` | 父进程只写 baseline，原本由 GraphRunner 首次运行时写计划；隔离 Worker 在重建容器阶段先读取计划，形成持久化顺序竞态 | `RunCoordinator.submit()` 在创建 Worker 前显式持久化 `plan.json` | 已增加回归覆盖，后续恢复仍以 Trace 文件为事实来源 |
| 空闲疑似后仍等到硬截止 | 宽限期判断嵌在当前 idle 条件分支内，状态文件变化后可能跳过终止检查 | 将宽限期判断提升到阶段状态读取之后、idle 检测之前，疑似状态一旦建立就独立倒计时 | 仍需在慢 Provider 和工具调用环境中做集成验证 |
| 恢复任务被旧进度快照误判为空闲 | 新 Worker 启动前会读取上一次运行留下的 `worker-status.json`，旧时间戳可能已超过阈值 | 监管只接受本次 Worker 启动时间之后的进度，Worker 启动时清理控制文件并写新快照 | API 重启恢复仍需在不同机器时钟下验证 |
| macOS `Process.join(timeout)` 轮询延迟 | Provider/CrewAI 子进程包含后台线程时，sentinel 等待可能长时间不返回，空闲和硬截止被拖延 | 改为有界 `Process.join(timeout)` + 短 sleep 轮询，并增加独立 Worker heartbeat | 仍需在真实 Docker/Provider 组合下测量监管延迟 |
| 慢 Provider 被误判为 Worker 失活 | LLM 长时间没有 chunk 时，旧逻辑把一次空闲观察和后续终止状态混在一起；真实进度恢复后旧计时不会清零 | 空闲先记录 `provider_stalled`；只有 `last_progress_at` 在整个 `PROJECTOS_PROVIDER_STALL_GRACE_SECONDS` 宽限期内不变才终止，真实进度恢复会重置计时 | Provider 真正卡死时仍会被终止；硬截止继续作为最终资源上限 |
| 工具成功后请求不正常收尾 | `ToolDef` 只有参数和执行模式，没有声明“成功调用是否就是当前节点终态”；CrewAI 将产物保存结果当作普通中间工具结果，继续 `post_tool_reasoning` 和下一次 LLM 调用，最终可能触发 `max_iterations` 或空响应异常 | 增加统一 `completion_policy`：产物保存、候选提交使用 `final`，文件读写和检查使用 `continue`；适配层将 `final` 映射为 CrewAI `result_as_answer=True` | 终态工具必须只在成功提交完整产物后返回；工具失败仍交给 CrewAI 处理，不会伪造完成 |
| 端口测试互相影响 | 测试依赖共享主机端口和权限，环境中已有占用或禁止 bind | 端口分配器可释放和复用；测试应在干净环境运行 | 当前受限环境的全量回归仍可能出现端口/Socket 错误 |

## 4. Worker 隔离与多维度监控

单一固定超时无法区分“模型还在输出”和“进程真的卡死”。现行运行状态分成以下层次：

```text
LLM SSE chunk -> Agent tool call -> artifact/checkpoint -> WorkItem -> Worker -> Trace
```

- `LLM(stream=True)` 打开 CrewAI 的流式处理；事件总线记录调用开始、chunk、完成和失败，但不保存 token 正文。
- `ExecutionContext.progress` 贯穿 Agent 和受控工具；工具开始/结束、chunk 数、字节数和调用计数写入 `.projectos/runs/<trace_id>/worker-status.json`。
- 父进程同时执行硬截止和阶段空闲观测。默认 LLM 空闲 120 秒、工具 300 秒、Sandbox 600 秒；先记录 `worker_idle_suspected/provider_stalled`，再进入 `PROJECTOS_PROVIDER_STALL_GRACE_SECONDS` 宽限期。只有 `last_progress_at` 在整个宽限期内没有变化才终止 Worker；heartbeat 只表示进程存活，不算业务进度。
- API 可从 `GET /runs/{trace_id}` 或 `GET /runs/{trace_id}/progress` 读取 `phase`、`last_progress_at`、`idle_for` 所需的原始时间戳和计数器。

这解决的是“无进度时可判定、持续有真实进度时不误杀”，不是无限延长任务。硬截止仍然存在，避免异常 Worker 永久占用资源。

## 5. 2026-08-25 工具终态与 Provider 监管收敛

### 暴露的问题

一次真实 Provider 调用中，架构工具已经成功写入 `architecture.md`，但 Agent 没有结束当前节点，随后又发起了额外 LLM 调用。重复调用会放大延迟和失败概率；当最后一次模型返回空内容时，CrewAI 抛出 `Invalid response from LLM call - None or empty.`。这不是 Provider API 本身失败，而是 ProjectOS 没有把“工具成功”转换成统一的节点终态。

同时，Worker 监管存在两个边界问题：

- `provider_stalled` 记录和真正终止之间的语义不清晰，文档还沿用了“只到硬截止”的旧描述；
- 一次空闲观察建立后，后续真实进度没有清除旧的停滞计时，可能把已经恢复的慢请求误判为持续卡死。

### 重构与解决方案

本轮不是在单个 Agent 中增加特殊判断，而是从四个层面重新收敛标准：

1. **工具合同层**：在 `ToolDef` 增加 `completion_policy`，只允许 `continue` 和 `final` 两种值，并在构造时校验。工具声明本身携带完成语义，Agent 不再自行猜测工具是否已经完成。
2. **执行适配层**：在 `ProjectOSTool` 统一把 `final` 映射为 CrewAI `result_as_answer=True`，把成功工具结果直接变成 Agent 完成结果；`continue` 保持原有多轮工具调用能力。工具执行失败不触发终态，仍由 CrewAI 进入错误处理/重试路径。
3. **Worker 状态层**：将 Provider 监管统一为“阶段空闲阈值 + 明确宽限期 + 硬截止”。首次无真实进度只记录诊断；宽限期内 `last_progress_at` 发生变化则重置计时；持续无变化才终止 Worker。heartbeat 只证明进程存活，不再被当作业务进度。
4. **配置与验证层**：将需求、架构、任务、环境、实现、测试、Review 产物保存和候选提交工具标记为 `final`，代码文件写入、读取、运行检查等迭代工具保留 `continue`；删除无实际执行意义的 `PROJECTOS_IDLE_GRACE_SECONDS`，统一使用 `PROJECTOS_PROVIDER_STALL_GRACE_SECONDS`，并同步 README、模块文档和回归测试。

### 验证结果

- 工具管理、进度监控、Worker 协调器回归测试通过。
- 全量测试：`247 tests, 2 skipped`。
- `compileall` 和 `git diff --check` 通过。
- 既有 FHL/OpenAI-compatible Provider 的文本、工具调用、工具结果回传和流式 `[DONE]` 行为已单独验证；本次修改只改变 ProjectOS 的终态映射和监管判定，不改变 Provider 协议。

### 此前五项系统级改进

这些改动解决的不是单次运行的异常，而是恢复、授权、输入边界、实现自由度和本地运行体验之间长期不一致的问题。

| 改进面 | 旧问题 | 重新设计 | 保留的边界 |
|---|---|---|---|
| 能力授权生命周期 | 授权只存在于当前 Worker 内存；Worker 或 API 重启后无法安全恢复，撤销也不能阻止后续重放 | 增加持久化 `CapabilityGrant` 账本；授权带 `node` / `trace` / `project` scope，并提供 `GET /runs/{trace_id}/capabilities` 与 `POST /runs/{trace_id}/capabilities/{grant_id}/revoke`。Worker 恢复时只重放仍有效的 grant | 授权本身仍由项目所有者决定；持久化只保证范围、恢复和撤销可审计 |
| 重试与局部重规划 | 失败后容易重做整条 DAG，既浪费成本也破坏已完成节点；流式无进度没有清晰的恢复边界 | Provider 无进度先记录阶段诊断，超过宽限期才终止当前 Worker，并保留 checkpoint。Repair Planner 接收 `repair_scope`，范围固定为失败节点、直接前置节点和直接后继节点；`FailurePackage` 同步携带该范围 | 局部窗口不能越过图依赖任意扩张；复杂跨域修复仍可能需要人工修改需求或重新发起运行 |
| 外部文档依赖 | Agent 会把一般 REST/HTTP 常识误判为需要外部文档，导致不必要 capability wait，也可能在未授权情况下猜测规范 | 只有 requirement Markdown 的 `## 外部规范` 显式声明主题时，控制面才允许 `external_documentation` 请求，例如 `REF: RFC 9110`；声明会被规范化写入需求元数据，后续节点按结构化输入使用 | 没有声明的外部规范不能由 Agent 自行扩张；声明不等于自动授权 MCP 来源 |
| CodeAgent 文件范围 | 精确文件白名单过窄，辅助模块、迁移、配置和常见工程文件会被拒绝，导致合同与真实工程结构无法同时满足 | Implementation Contract 支持 `allowed_roots`，在授权目录内可新增辅助模块、配置、迁移和常见工程文件；Git 暂存白名单补充 `.sql`、`.graphql`、`.proto`、`.conf`、`requirements.in`、`Makefile` 等 | 仍禁止越出授权目录、修改控制面文件或绕过分区 Git worktree；路径自由度不等于取消架构合同 |
| 开发/生产运行模式 | 生成项目的运行体验与开发调试脱节，FastAPI 修改后必须手动重启；同时不能牺牲生产运行的稳定性 | `runtime.yaml` 支持 `mode: development`；受信 FastAPI 运行器在该模式自动注入 `uvicorn --reload`，`production` 保持无 reload。两种模式共用端口租约、健康检查、停止和端口释放 | reload 只属于开发模式；容器、端口与健康检查的控制面边界保持一致 |

## 6. 记忆、Trace 与恢复

Memory 负责会话和执行上下文，Trace 负责控制面事实，checkpoint 负责恢复状态。三者不能互相替代：

- Memory 保存输入、Agent 输出和工具审计，供后续节点和对话恢复参考；
- Trace 保存计划、事件、状态和 Sandbox evidence；
- checkpoint 保存可重放的 `RunState`，只恢复未完成 WorkItem。

模型正文不写入流式进度文件，避免状态文件膨胀；详细内容仍在 Memory/Artifact 中按需检索。

## 7. 当前剩余风险

1. CrewAI 和 Provider 的流式能力依赖具体模型；若 Provider 不返回 SSE，系统会记录 `llm_request`，但不能伪造 chunk 进度。
2. 端到端闭环仍需要在有 Docker daemon、镜像缓存和可用 API key 的干净环境验证；受限开发环境的端口测试失败不等于业务逻辑失败。
3. Planner 的动态路径需要真实评测集衡量节点覆盖率、重复运行稳定性和修复成功率，不能只凭单次 Demo 判断。
4. Worker 被终止后可以从 checkpoint 恢复，但当前没有跨主机租约和外部队列，仍属于单机可恢复架构。

后续优先级应是：稳定评测集、真实 SSE/工具/产物事件的集成测试、干净 Docker 环境的全链路证据，然后再考虑外部队列和跨主机 Worker。

## 8. 2026-08-25 终态、对象契约与常见故障修复

### 复现到的故障

- 新需求“管理员库存调整接口”被会话路由成修改请求：宽泛的“调整”命中规则把业务词误认为会话动作。
- BootstrapAgent 返回 `prepare_environment、save_environment` 时，控制面把整个字符串当成一个 MCP 能力查询，最终错误阻塞。
- Provider 返回 HTTP 402 或连接异常时，Planner 只抛出底层异常，会话没有可见的 assistant 诊断。
- `/resume` 已开始新 Worker，但 Trace 仍保留历史 `failed`，API 同时展示 `failed` 与 `running`。
- Worker watchdog 曾把“无 chunks”与“无进度”混用，正常模型思考、空响应、连接断开无法区分。

### 处理方式

1. Agent 边界新增 canonical capability 规范化；复合环境工具请求映射为 `environment_preparation`，由控制面直接准备并保存环境，不进入外部来源审批。
2. 会话修改意图只接受明确的修改句式；动态 Planner 的任意 Provider 异常统一转为 `PlannerFailure`，并追加可读诊断消息。
3. TraceStore 增加 `mark_running()`；恢复提交前清除历史终态和错误，保证持久状态先于 Worker 启动迁移。
4. `ProgressTracker` 持久化 `llm.state` 和 `terminal_at`，补齐 `agent_completed/agent_failed`；正常 `execute_task` 返回是非流式 LLM 的明确终态，异常则写 `llm_failed`。
5. watchdog 只在显式 LLM call 处于 `started/streaming` 或工具/沙盒阶段时观察空闲；heartbeat 不算业务进度，终态状态不会触发 Provider stall。

全链路对象的字段语义、约束和边界见 [standard-objects.md](architecture/standard-objects.md)。

## 9. 2026-08-25 交付契约与运行前置检查

### 暴露的问题

真实库存平台链路暴露了四类结构性缺陷：项目文档节点把后续 `environment.md`、`tests.md` 和
`review.md` 当成自己的 required paths；前端入口、Dockerfile 和数据库初始化脚本由 Compose 引用却
没有生成；IntegrationAgent 把运行完整性问题当成 Git 合并阻断；TestAgent 在 sandbox 未就绪时仍被
包装成 completed。

### 系统级改进

1. **DeliveryContract**：每个最终产物只有一个 owner，并声明所属阶段。`TemplateCompiler` 和
   Implementation Contract 展开阶段校验 owner 节点，未来节点的产物不能成为当前节点的必需输入。
2. **ProjectRuntimePreflight**：在测试前静态检查 Compose build context 的 Dockerfile、数据库初始化
   入口和前端 HTML 本地资源引用。该检查不依赖模型自述，失败会生成 `runtime_preflight` 阻塞信号。
3. **分级 Integration Review**：LLM findings 统一带严重级别。LLM 不能凭自然语言改变确定性 Git
   合并规则；可运行性风险进入后续 preflight/tests/review，不再用关键字猜测阻断级别。
4. **真实测试状态**：`setup_failed` 是环境未就绪，不是测试完成。控制面保存 SandboxEvidence 后将
   Trace 标记为 BLOCKED，修复环境后从 checkpoint 恢复，避免产生虚假的测试闭环。

### 验证

- 新增 `tests/test_delivery_contract.py` 和 `tests/test_runtime_preflight.py`，覆盖产物 owner、缺失
  owner、前端引用、Dockerfile 和数据库初始化入口检查。
- 更新 GraphRunner 测试，确认 sandbox setup failure 在 Review 前阻塞，而不会生成 completed 测试节点。
- 本地回归：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，`261 tests, 2 skipped`；
  `compileall` 与 `git diff --check` 通过。

## 10. 2026-08-25 库存平台全链路实测

本次以 `inventory_delivery_full_v2` 运行生产级库存与订单预留平台，实际走过：

```text
requirement → architecture → architecture-contract → tasks-plan
→ tasks-integration → tasks-quality-gate → environment
→ dependency approval/cache → checkpoint resume
→ 8 个并行 CodeAgent 分区 → code-integration
```

环境审批先后暴露了两个可复现边界：依赖审批必须绑定 `requirements.in` 摘要；冷启动 ARM Docker
环境中依赖下载可能超过固定 120 秒。审批状态已经按摘要校验，依赖解析器改为 `--no-cache-dir`，
并支持 `PROJECTOS_DEPENDENCY_RESOLVE_TIMEOUT_SECONDS`（默认 600 秒）。随后 8 个分区均产生真实
Git ChangeSet，证明并行拆分和持久化 worktree 已经生效。

集成阶段最终拒绝合并，原因是真实实现缺口：基础设施 ChangeSet 没有 `backend/migrations`，测试引用了
领域 ChangeSet 未提供的 `app.domain.inventory` / `app.domain.order`。另发现并修复确定性策略的边界：
合同中的 `backend/app/domain` 这类无尾斜杠目录不应按单文件检查。修复后新增回归测试通过；剩余失败是
真实代码/合同缺口，而非策略误报。

局部修复尝试进一步证明：受控 Workflow 不能直接复用普通 `PlanPatch`，需要单独的受控修复计划或重新
运行受控模板。当前 Trace 因此以 `failed` 保留完整证据，没有伪造 `tests.md` 或 `review.md`。

验证：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，`262 tests, 2 skipped`。

## 11. 2026-08-25 分层 Wave 与完整文件所有权

实现合同新增 `wave` 和 `owned_files` 字段。编译器会把显式或依赖推导出的 Wave 编译为 DAG
屏障：同一 Wave 内的实现单元仍可并行，不同 Wave 必须等待前一 Wave 完成。后继 CodeAgent
会收到前置实现单元的 staged ChangeSet 引用，`load_code_input` 返回真实变更文件内容，而不再
只有 `completed` 状态摘要。

`owned_files` 将完整文件所有权写入合同并在合同解析、staging 写入和 ChangeSet 阶段校验；一个
文件只能由一个实现单元负责，禁止多个 Agent 协作写同一文件的不同片段。Integration 在 Git
三方合并后增加最低语义检查，扫描 Python 本地 import 是否引用不存在的模块，并把路径/实现/集成
责任分类写入阻塞错误。

### 真实验证结果

`inventory_contract_e2e_20260826_v2` 验证了自愈路径：依赖环境从 `dependency_failed` 恢复为
`ready`，随后代码 Wave 正常推进。最终 Integration 根据真实 ChangeSet 拒绝合并，因为 API 文件
引用的应用层和基础设施模块尚未完整落盘。系统因此停在 Integration，没有生成虚假的测试或 Review；
剩余问题属于 CodeAgent 交付完整性，不再属于 Docker 环境问题。

## 12. 依赖环境自愈

活动报名平台 Trace `tr-71898419e4db` 在依赖批准后仍然阻塞。Docker daemon 和基础镜像均正常，
实际 stderr 同时出现 PyPI SSL/连接中断、pip 对宽版本范围回溯到旧包，以及容器 `/tmp` 的
`No space left on device`。创建一个新的同配置容器只能重放问题，不能构成恢复策略。

依赖解析器现在把失败分为 `storage`、`network`、`incompatible`、`timeout` 和 `unknown`。每次尝试
仍使用全新 `--rm` 容器，但按原因调整策略：优先使用 binary wheel 避免不必要的源码构建；网络失败
扩大 pip 重试和连接超时；存储失败或最终源码回退使用项目 `.sandbox/recovery/<digest>` 下的临时目录
挂载 `/tmp`，结束后自动删除。每次失败都会清理当前 digest 的半成品 wheel，只有成功写入
`.projectos-ready` 的缓存才能进入测试和运行阶段。

`preparation.json` 同步记录 `failure_kind`、`attempts` 和 `recovery_actions`，使 checkpoint 恢复和 API
调用方能区分“系统尚在自愈”与“已耗尽安全恢复动作”。控制面只清理本项目生成的临时资源，不自动
执行 `docker system prune`，因为全局清理可能破坏其他项目的镜像、卷和构建缓存。

## 13. 2026-08-26 文件级交付边界收敛

### 暴露的问题

真实 Trace 中 Architecture Contract 把 `backend/application/**`、`scripts/**` 等目录 glob 放进
`owned_files`。编译器虽然尝试按路径拆分，但目录本身被当成一个可交付文件，随后 ChangeSet、Artifact
ID 和 Integration 的责任判断全部失真。另一个隐蔽问题是 API 单元拆成多个文件后，编译器把
`main.py` 的入口要求注入到 routes/schema 子任务，导致同一 Wave 的任务互相要求对方文件。

### 系统级改进

1. **合同解析先拒绝伪文件**：`owned_files`、`required_paths` 和顶层 `required_files` 拒绝 glob 或目录值；
   `required_paths` 必须属于本单元的 `owned_files`。目录 glob 只保留在 `allowed_paths`/`allowed_roots`。
2. **编译器以完整文件为最小 WorkItem**：多文件单元按具体路径拆成一个文件一个 CodeAgent，保留原
   Wave、依赖和物理 `output_slot`；缺少 ownership 的代码单元直接失败，不再静默退化为目录任务。
3. **入口要求按 owner 归属**：只有拥有入口文件的 WorkItem 才会收到该入口的 required gate，避免
   `main.py`、routes、schema 之间形成无法同时满足的交付合同。
4. **交付门精确核验**：CodeAgent ChangeSet 必须只包含唯一 owned 文件；Runner 对路径做精确匹配，并
   对合同声明的 `provided_symbols` 做 AST 名称检查。Integration 继续负责跨文件 import 和接口语义。
5. **输入合同和 Agent 提示词统一**：TaskInputPackage、ArchitectureContractAgent 和 CodeAgent 都明确
   “目录是授权、文件是交付、符号是协作”，模型不能用自然语言或目录存在性代替落盘事实。

### 验证

- 新增 glob 拒绝、required 文件归属、入口 owner、单文件 WorkItem、物理分区保持和符号门禁回归测试。
- 全量回归：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，`286 tests, 2 skipped`；
  `compileall` 与 `git diff --check` 通过。

## 14. 2026-08-26 ProjectContract 单一事实源

此前 `layer-contract.json` 与 `implementation-contract.json` 同时描述架构边界：前者保存层级、
依赖方向、禁止导入和路径映射，后者保存入口、接口、Wave 和文件 owner。两份合同会让 Planner、
Policy、CodeAgent 和 Integration 读取不同来源，属于架构债务。

现在统一为 `.projectos/architecture/project-contract.json`。ProjectContract 同时包含两组字段，
Compiler、Policy、CodeAgent、Integration 和 Review 都只读取这一份项目合同。`WorkItem` 只是合同
编译后的单文件执行投影；`DeliveryContract` 仅是 ProjectOS 控制面的流水线产物清单，不再作为项目
架构合同。

旧 `implementation-contract.json` 仅允许历史 Trace 只读加载，新运行不会写入；新的 Architecture
Contract 节点不再解析 architecture Markdown 内嵌合同，也不再生成独立 layer-contract.json。

## 15. 2026-08-26 ProjectContract 输入形态与 FHL 全链路复测

统一合同后的 FHL 实测首先在需求节点因 Provider 余额不足失败；切换到有余额的
`gpt-5.6-terra` 后，需求和架构均成功落盘，流式监控也正确区分了长时间停顿与真实进度。
合同节点随后暴露了输入 schema 的可用性问题：模型把顶层 `layers`、`forbidden_imports` 和
`path_mapping` 生成了不同于标准合同的形态，并将工具校验错误误报为外部能力请求，Trace 按设计
阻塞而没有伪造合同或继续执行。

本轮修复保持标准合同边界不变：

- `_strings()` 的错误现在报告真实字段路径，避免把顶层字段误报为
  `ImplementationUnit.layers`；
- `ArchitectureService` 只对 `layers` 做无损归一化，接受 `{name/id/layer: ...}` 的目录对象并
  最终保存为字符串数组；依赖和路径映射仍要求以顶层 `layers` 为 key 的严格对象；
- ArchitectureContractAgent 提示词增加可直接照抄的 ProjectContract JSON 骨架，并明确工具 schema
  错误必须修正后重试，禁止伪造 `capability_request`。

回归测试覆盖了对象/数组层目录归一化和字段级错误信息。两次 FHL Trace 均保留原始事件与
checkpoint：一次记录 Provider 402，另一次记录合同输入不符合 schema 的阻塞。它们证明当前链路
能够完整运行到合同边界并正确停止；要继续验证代码 Wave、Integration、Tests 和 Review，需在
模型按新 schema 成功保存合同后重新发起一条 Trace。

## 16. 2026-08-26 FHL gpt-5.6-terra 复杂项目复测

使用 FHL `gpt-5.6-terra` 对“库存与订单分析平台”执行完整
`project_delivery` 计划（Trace `tr-4f2812ea8132`）。FHL 流式连接持续产生真实进度，需求节点约
72 秒完成，架构节点约 106 秒完成；因此本次没有触发连接中断、空闲误杀或 Docker 前置问题。

合同节点发生 1 次受限重试（共 5 次 LLM 调用、约 9,800 个 chunks、4 次工具调用）。两次尝试都实际
调用了 `save_implementation_contract`，但输入均未通过校验，未生成合法的
`.projectos/architecture/project-contract.json`。系统在重试额度耗尽后将 Trace 标记为 `failed`，
没有继续执行任务、代码、测试或审查，也没有生成虚假的交付文件。

该结果进一步确认当前主要瓶颈是 FHL 对严格 ProjectContract 工具调用的收敛性，而非 Worker 监控或
网络连接。修复前回归测试为 `289 tests, 2 skipped`；服务在 `127.0.0.1:8010` 保持健康。

## 17. 2026-08-26 ProjectOS 工具协议核验

对 FHL 失败 Trace 的工具事件和运行时注册表进行逐层核验后，确认“找不到合法工具调用”并不是工具
未注册：`architecture_contract` domain 实际暴露 `load_architecture`、`load_requirement` 和
`save_implementation_contract`，CrewAI 也将它们转换为 OpenAI-compatible 的
`type=function`、`strict=true` 定义；Trace 中四次保存调用均已到达 ProjectOS，本地错误分别是
`InterfaceContract.interface_id 不能为空`、`InterfaceContract.name 不能为空` 和接口
`owner_unit` 不存在。

核验同时发现适配器存在一个独立的协议不一致：未声明 `additionalProperties` 的 ToolDef 在本地
Pydantic 模型中默认为开放对象，但 CrewAI 发给 provider 时会强制改成关闭对象；带默认值的可选
参数则可能被发送为“required 的 string + null 默认值”。这会让 provider schema 与本地执行校验
不一致，增加严格函数调用失败的概率。

修复内容：

- ToolDef 默认使用关闭对象，直接调用和 provider 看到的 schema 保持一致；
- 可选标量字段显式生成 `string|integer|number|boolean|null`，兼容 CrewAI 将属性全部标记为
  required 的 strict serializer；
- `configure_runtime`、`run_sandbox_check` 在收到 provider 的 `null` 后归一化为领域默认值；
- 新增工具注册和 wire schema 回归测试。

该修复解决的是工具协议层的潜在问题；FHL 合同 Trace 的直接失败原因仍是模型生成的合同内容不满足
ProjectContract 语义校验，不能把两者混为“工具找不到”。

修复后全量回归：`292 tests, 2 skipped`。

## 18. 2026-08-26 Project Contract 结构化输入收敛

### 暴露的问题

Architecture Contract 工具此前接收一个 `content: string`，模型需要先拼接并序列化整份
JSON，再由控制面二次解析。这个边界导致嵌套字段和层级映射无法被工具 schema 约束；语义
错误只能返回普通文本，Runner 容易把它误判为外部能力缺失；`layers` 的动态 key map 也
无法提供精确的错误路径。

### 重构与解决方案

- 新增 `ProjectContractInput` 及固定子对象 DTO。工具 wire 参数统一为
  `{contract: {...}}`，层级规则使用 `layers[]` 对象数组，不再依赖动态 key map。
- CrewAI 适配器递归转换 JSON Schema，支持嵌套对象、对象数组、`$defs/$ref` 和 nullable
  值；对象默认 `additionalProperties=false`，本地 Pydantic 校验与 strict function schema
  保持一致。
- 控制面先将 DTO 归一化为现有 `ImplementationContract` canonical dict，再执行依赖、接口、
  路径、循环和完整文件所有权校验；通过后原子写入唯一的
  `.projectos/architecture/project-contract.json`，不新增第二份合同。
- 合同存储使用同目录临时文件、`fsync` 和 `os.replace` 完成原子替换；进程重启时只能读取
  旧的完整合同或新的完整合同，不会读到截断 JSON。
- 保存工具返回包含 digest、单元数和接口数的机器可读成功摘要；失败返回
  `error_type`、JSON path、错误码和消息。失败不会写入合同文件，终态工具会抛出带原始 JSON
  诊断的异常，使 Runner 继续在当前节点局部重试而不触发 capability approval。
- Architecture Contract Agent 与 Runner 提示词同步改为结构化 `contract`，要求根据
  `errors[].path` 修正同一对象，禁止把 schema 错误转换为 `capability_request`。

### 验证

- 新增结构化合同工具成功落盘、语义错误路径和失败不写入回归覆盖。
- 全量回归：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，`294 tests, 2 skipped`；
  `compileall` 与 `git diff --check` 通过。

## 19. 2026-08-28 失败证据到局部修复闭环

### 真实 Trace 暴露的问题

活动票务平台 Trace `tr-36215b029dfe` 已完成需求、架构、分层并行实现、Wave 合并和真实
sandbox 测试。`web-unit` 通过，但 Python `unit` 在收集阶段失败：
`tests/infrastructure/test_postgresql.py` 导入 `IdempotencyEntry` 时，实际实现文件
`backend/app/application/ports.py` 未提供该符号。第一次局部修复又出现两个控制面问题：

- EXCLUSIVE 修复节点被通用 PARTITIONED 提示词误导，错误声称没有 `write_workspace_file`；
- 修复计划只保存了 evidence ID，未把失败责任文件写入 `allowed_paths`；同时 CodeAgent 被要求
  读取 `implementation.md`，但代码领域产物白名单将其拒绝，导致模型反复读取诊断而未落盘，
  最终触发 provider stall 保护。

### 修复

- CodeAgent 明确区分互斥执行协议：PARTITIONED 使用 `write_staged_code_file`，EXCLUSIVE 修复
  使用 `read_workspace_file` 与 `write_workspace_file`；错误提示按模式选择工具。
- `FailurePackage` 增加结构化 `as_task_data()`；TaskInput 同时提供机器可读失败包和人类可读摘要，
  不再只依赖截断 stdout/stderr。
- TraceStore 从持久化 sandbox evidence 自动提取 `backend/...`、`frontend/...` 等实现路径，
  将 Python `app.foo.bar` 导入映射到 `backend/app/foo/bar.py`，并排除 `tests/**`。
- Planner 将 `repair_paths` 回填为 CodeAgent 的 `allowed_paths`，自动加入
  `tests/**`、`.projectos/**`、`project.yaml` 和 `runtime.yaml` 禁止范围。
- API/Worker 恢复历史计划时重新计算 FailurePackage 和路径边界，并原子持久化补全后的计划；
  `implementation` 作为 CodeAgent 只读历史产物开放，不扩大写权限。

### 验证

针对性和编排回归共 `66 tests` 通过；真实证据 `ev-fe8374409c4e` 可稳定解析为：
`backend/app/application/ports.py`、`backend/app/infrastructure/repositories.py`。下一步使用
该 Trace 恢复时，预期顺序为 `EXCLUSIVE CodeAgent 落盘 -> TestAgent 重新执行 unit/web-unit`
，再进入最终 Review；若模型再次停滞，证据将明确区分 provider stall 与修复协议失败。

## 20. 2026-08-29 质量合同推导与过期审查证据修复

### 暴露的问题

活动票务平台 Trace `tr-36215b029dfe` 的实现文件和测试实际上已经满足架构合同，但
`ProjectQualityPolicy` 仍报告三个问题：`backend/app/main.py` 是合同声明的后端入口却未重复列入
`path_mapping`，`backend/worker.py` 是生成的独立 worker wrapper 却未被识别为 operations，
以及 `tests/infrastructure/test_postgresql.py` 通过目录、`asyncpg` 和 `DATABASE_URL` 表达集成测试，
没有出现字面量 `postgresql_integration`。恢复时还发现终态检查搜索旧 `review.md` 中的
`status=failed` 文本，导致策略修复后仍被历史报告阻塞。

### 修复

1. **有效合同路径**：Policy 构造只读的 effective path mapping，将 entrypoint、接口 owner 文件和
   已存在的 worker wrapper 合并到相应层；未声明且无合同语义的 backend/frontend 文件仍触发边界错误。
2. **语义测试证据**：测试类型同时检查路径和内容，支持 PostgreSQL 驱动/连接串、并发目录和常见
   前端测试语义，不再要求模型写入策略专用魔法字符串。
3. **当前事实优先**：Runner 终态质量门每次重新计算当前 workspace 的 Policy，不再把历史 review
   附录当作当前状态；策略修复后会替换旧质量附录，避免重复追加并同步过期的 fallback 阻塞结论。
4. **证据摘要去重**：Review fallback 只展示每个 check 流的最新 sandbox 结果和失败诊断，
   完整 stdout/stderr 仍保留在独立 evidence JSON 中，避免历史重试日志淹没当前结论。
5. **追踪矩阵初始化**：需求快照会初始化 AC 矩阵；解析器支持 `1. **AC-1 ...**`、标题和普通
   列表格式，同时避免把正文中的交叉引用误注册为新的验收项。

### 验证

- 控制面回归：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`，`313 tests, 2 skipped`。
- 历史项目 Policy 从 3 个误报变为 `passed`；真实 FHL Worker 恢复同一 Trace 后从 `blocked` 进入
  `completed`，`.projectos/delivery/state.json` 为 `delivery_ready`。
- `review.md` 已由确定性控制面刷新为单一 `## 确定性质量策略`、`status=passed` 和 `PASS` 结论；
  原始失败尝试仍可在 `.projectos/runs/tr-36215b029dfe/evidence/` 追溯。

## 21. 2026-08-29 Node 测试发现规则收敛

### 暴露的问题

宿主机复测发现生成项目的 `frontend/app.js` 与旧测试文件契约不一致：测试期望 `/api/events` 和
旧 DOM 标识，而当前页面实现使用 `/events` 和新的元素结构。更关键的是，sandbox 的 `node --test`
无参发现只执行 `*.test.js`，控制面却把 `test_frontend.js` 也算作有效测试，导致只执行了一个回归文件，
漏掉前端失败并错误记录为 passed。

### 修复

`SandboxPolicy` 现在以同一白名单发现 Node 测试文件，并将完整的 workspace 相对文件列表作为
`node --test <files...>` 的固定参数；`SandboxController` 的空测试检查复用同一发现函数，不再出现
“预检认为有测试、实际命令没跑测试”的分叉。文件列表来自受控 workspace，Agent 不能注入宿主命令。

### 验证与剩余边界

- 控制面全量回归：`313 tests, 2 skipped`。
- 对活动票务项目直接运行全部前端测试后，发现 6 个真实失败，证明假阳性已被消除；该项目的
  `application_infrastructure_scope.test.js` 单测通过，但 `test_frontend.js` 需要由后续 CodeAgent
  按当前页面/API 合同修复，不能继续把旧测试漏跑当作交付通过。
- Python 领域、应用和 API 测试宿主机通过（`31 passed`）；PostgreSQL/并发测试仍需生成项目依赖缓存和
  PostgreSQL 服务，宿主 `.venv` 缺少 `sqlalchemy` 时应由 sandbox 环境负责安装。
## 22. 2026-08-29：FHL 真实链路中的代码分区工具误报

会议室预约平台的真实 FHL Trace `tr-6a0db0999ba0` 在合同修复后成功展开 31 个文件级实现单元，
并按 wave 完成了领域、应用、基础设施和接口前置文件。随后 `wi-code-u-interface-dependencies`
连续三次返回“当前工具集中未提供 `load_code_input` 和 `write_staged_code_file`”，但控制面实际已
注册并成功执行过 `load_code_input`；问题不是授权或路径边界，而是 OpenAI-compatible tool loop 在
一次写入前继续开新回合，模型把本地工具误判为能力缺口。

处理：将 `write_staged_code_file` 的 `completion_policy` 固定为 `final`，成功提交 ChangeSet 后
立即结束当前 CodeAgent 回合；在 CodeAgent 合同中明确该工具调用是分区节点终态。失败仍由文件级
落盘门禁触发有界重试，不放宽路径授权，也不生成控制面之外的文件。

## 23. 2026-08-29：分区代码读取与写入终止语义校正

### 暴露的问题

真实 FHL 运行再次出现“没有生成 Git ChangeSet”。事件显示 CodeAgent 先成功调用
`load_code_input`，随后直接结束；检查工具注册发现读取工具被错误标记为 `completion_policy=final`，
而真正应该终止节点的 `write_staged_code_file` 没有固定为终止工具。修正后，模型在长上下文重试中又
连续读取多个引用，仍可能耗尽回合而不写文件。

### 修复

- `load_code_input` 恢复默认 `continue`，允许“读取 -> 写入”的连续工具调用。
- `write_staged_code_file` 明确标记 `final`，ChangeSet 成功提交即结束分区节点。
- 分区 CodeAgent 重试改用短协议，只保留 WorkItem、唯一 `owned_files`、授权引用、写入工具和失败原因，
  不再重复注入完整架构/ChangeSet 正文。
- 第三次及之后的文件交付重试通过 `ExecutionContext.tool_allowlist` 只暴露
  `write_staged_code_file`，避免模型在读取工具上循环；该限制仅作用于重试，不改变首次执行的工具合同。

### 验证

工具合同和编排回归测试 `51 passed`。同一真实 Trace 已重启 API 并恢复验证；修正前的事件、模型输出和
失败原因继续保存在 `.projectos/runs/tr-6a0db0999ba0/`，待本轮重试完成后以 ChangeSet、sandbox 和 Review
证据确认最终闭环状态。

## 24. 2026-08-29：集成检查兼容 Python namespace package

### 暴露的问题

余额恢复后的真实会议室预约 Trace 已完成接口依赖、迁移和前端状态等实现 Wave，但 Wave 9
在集成语义检查被拒绝：`backend/app/main.py` 的 `from app.infrastructure import lifecycle`
被报告为引用不存在的 `app.infrastructure`。实际目录中已有 `config.py`、`database.py`、
`identity.py` 和 `repositories.py`；该项目采用 Python 3 namespace package，没有为每个目录生成
`__init__.py`，因此不是实现文件缺失。

### 修复

`GitCodeIntegrationService._validate_local_imports` 现在为每个已存在的 Python 文件登记所有父级
包模块（例如 `backend.app.infrastructure.database` 同时登记 `backend.app.infrastructure`）。
这样既支持有 `__init__.py` 的传统包，也支持合法的 namespace package；真正不存在的叶子模块
仍然会被拒绝。新增回归测试覆盖无 `__init__.py` 的 `from app.infrastructure import database`。

### 验证

代码集成测试 `13 passed`；该修复尚未在真实 Trace 上恢复，因为当前 Trace 已停在修复前的失败
checkpoint，下一次余额充足的恢复将验证 Wave 9 是否通过并继续后续测试、Review。

## 25. 2026-08-30：Worker 中止后的分区节点恢复协议

### 暴露的问题

真实 Trace 在代码实现已经进入测试 Wave 后，`wi-code-u-test-api` 持续重复读取前置 ChangeSet，
没有完成文件写入，最终由 Worker 900 秒总时限终止。仅依赖新的首次执行提示会在 checkpoint 恢复时
再次走同一读工具循环；该问题与 FHL 余额或集成语义无关。

### 修复

Runner 现在把 Trace 中持久化的 `worker_timed_out` / `provider_stall_timeout` 视为恢复信号。此类
Trace 的待执行 PARTITIONED CodeAgent 从第一轮恢复就使用短的写入优先协议，并只暴露
`write_staged_code_file`；已有前置内容仍保留在 checkpoint、ChangeSet 和 Memory 中，恢复不会扩大
路径或文件所有权。

### 验证

控制面针对性回归 `52 passed`。该协议随后作用于会议室预约 Trace 的恢复流程；本次真实
Trace 已完成后续 Tests/Review，具体的 repair 基线恢复问题和最终证据见第 26 节。

## 26. 2026-08-30：Repair 计划提前结束交付链

### 暴露的问题

真实 FHL Trace `tr-6a0db0999ba0` 在 `tests` 失败后进入局部 repair。repair Worker
完成后，控制面把 repair DAG 写入了同一个 `plan.json` 和 `checkpoint.json`，并将 Trace
直接标记为 `completed`，因此原始 `wi-09-tests`/`wi-10-review` 没有机会继续执行，
`review.md` 也没有产出。该问题与模型余额无关，是恢复状态的持久化边界错误。

### 系统级修复

1. `TraceStore` 增加独立的 `delivery-plan.json` 和 `delivery-checkpoint.json`。原始交付
   DAG 与局部 repair DAG 分开保存；每次 repair 前先保存原始计划及已完成节点。
2. Worker/API 恢复优先加载交付基线，并从 checkpoint 继续未完成 WorkItem。历史 Trace
   如果没有新字段，会从 `plans/` 中选择非 repair 计划，并依据 `work_item_completed`
   事件迁移已完成节点，不删除任何历史证据。
3. repair 成功后显式恢复原始计划、重新标记 Trace 为 running，再继续 tests/review；只有
   原始 DAG 通过 Review、确定性 Policy 和需求追踪矩阵后才能进入 completed。
4. 新建和 PlanPatch 入口在提交 Worker 前写入交付基线；架构合同展开后更新基线，保证
   动态 41 节点计划而不是未展开模板成为恢复对象。

### 真实验证

同一条历史 Trace 在 FHL 余额恢复、网络许可后成功迁移并完成：原始 41 个 WorkItem 中
38 个历史节点被恢复，随后环境确认、全量 unit sandbox 和 review 顺序执行，最终生成
`review.md`，Trace 状态为 `completed`，Policy `project.quality.v1` 通过。

## 27. 2026-08-30：Node/TypeScript 技术栈跨栈闭环边界

### 测试目标

新建项目 `node_order_reservation_ts_20260830`，要求 Node.js 22 + TypeScript + Fastify
后端、React + Vite 前端、PostgreSQL、Redis、Prisma、并发库存扣减、前后端分离、独立脚本
和完整测试。Trace 为 `tr-ac54aa25e361`。

### 实际链路

需求、架构、ProjectContract、任务规划和任务质量门均成功完成。第一次运行在架构节点因
Provider 长时间无进度被监管终止；第二次恢复继续完成架构和合同；延长 Provider 宽限期后
合同和任务节点通过，说明 checkpoint 恢复和长响应监管有效。

最终在环境节点以结构化能力请求阻塞：

```text
capability=nodejs-22-typescript-fastify-runtime
candidates=[]
```

项目只生成了 `runtime.yaml: profile: python-stdlib`，没有进入代码、集成、测试和 Review，
因此没有生成虚假的 `review.md`。

### 边界与根因

1. `RuntimeCatalog` 当前只有 `python-stdlib` 和 `python-pip`；虽然 Node 镜像仅被作为
   前端 `web-unit` 辅助检查使用，但没有 Node 后端/TypeScript 构建和 npm 依赖安装 profile。
2. `ApplicationCatalog` 只注册 Python/FastAPI/static-web 应用，没有 Fastify、Vite dev/serve
   和 Redis 多服务组合；环境节点无法安全接受 Agent 自定义 Docker 命令，因此必须阻塞而不是
   猜测一个 Python 替代实现。
3. 依赖审批与缓存只理解 `requirements.in`/pip，尚未有 `package.json`、lockfile、npm cache
   的摘要审批和隔离安装协议。
4. 当前 Policy 的 Python AST 分层检查不能直接验证 TypeScript import、ESM/CJS 边界、Prisma
   schema 和 React 交互；跨栈质量门需要独立的语言适配器。

### 结论与后续方向

ProjectOS 的控制面、Planner、合同、checkpoint 和能力审批边界对未知技术栈的行为是正确的：
它能完成规划并在缺少受信运行时来源时可解释地暂停，但当前“可用闭环”仍主要覆盖 Python
生态。支持 Node/TypeScript 需要新增受信 RuntimeProfile、ApplicationProfile、npm 依赖审批
与缓存、TypeScript/前端质量检查和 PostgreSQL+Redis 服务编排；在这些能力落地前，不能把
跨栈项目标记为交付完成。

## 28. 2026-08-30：Python 物流业务复杂度下的合同生成边界

### 测试目标

项目 `python_logistics_ops_20260830` 使用异步 FastAPI、SQLAlchemy async、PostgreSQL 和
静态 HTML/JavaScript 前端，实现运单分拣、并发领取、状态机、幂等扫描、主管统计、迁移、
独立启动脚本和完整测试。Trace 为 `tr-2ca426449c93`。

### 实际结果

需求、架构和第一次任务输入均开始正常执行；需求与架构产物成功落盘。ProjectContract 第一次
尝试返回 `architecture_contract_missing`，触发有界重试；第二次尝试持续产生大量流式输出，
但超过 Worker 900 秒硬上限，Trace 以 `failed` 结束。项目尚未进入环境准备、并行代码、集成、
测试和 Review，没有生成虚假的交付报告。

### 结论

这不是 Python runtime 不支持，也不是依赖审批或 Docker 故障，而是复杂业务约束下合同节点的
输出收敛性和响应时长仍然不足。现有 watchdog 能记录 `worker_idle_suspected`、
`provider_stalled` 和 `worker_timed_out`，checkpoint 可恢复；但恢复需要再次向外部 Provider
发送项目上下文，必须经过明确的外部出站授权。后续可考虑为合同节点增加结构化草稿分段、局部
合同校验重试和按节点复杂度配置硬上限，降低一次超长调用耗尽整条链路的风险。

## 29. 2026-08-30：分层架构对象协议

### 设计动机

此前架构分区之间主要传递 Markdown，下一层需要从正文重新推断模块、接口和约束；一处字段
命名或语义漂移就可能在合同节点才暴露，并把整个 Code 阶段挡住。架构设计现在增加固定三层
对象协议：`ArchitectureBlueprint`（depth=0）、`ModuleDesign`（depth=1）和
`ImplementationDesign`（depth=2）。

### 实现

- 每层使用 Pydantic closed DTO，统一 `design_id`、`parent_design_id`、`module_id`、
  `requirement_ids` 和接口引用语义；depth 只能是 0、1、2。
- 新增 `architecture_layered` 受控流程：L0 单节点生成总体蓝图，L1 三个模块并行细化，
  L2 对应模块并行生成完整文件 ownership 和测试边界，随后由
  `ArchitectureDesignBundle` 做父子关系、模块覆盖、接口唯一性和单文件 ownership 校验。
- Architecture Integration 只组合已授权 staged JSON 对象，并生成稳定 Markdown 候选；不重新
  设计业务、不写代码。现有 ArtifactRef、WorkItem、候选和质量门机制保持不变。
- 默认 `project_delivery` 暂不切换，避免影响已有 Python 闭环；新模板可先用于评测和逐步替换。

### 验证

新增结构化对象、三层深度、单文件 ownership、模板屏障和真实 staged 集成测试；编译检查通过。

## 2026-08-30：复杂交付统一走结构化架构合同

### 问题

复杂项目原先可能由 Planner 选择旧的 `project_delivery`，再由
`architecture_contract_agent` 从 Markdown 重新生成 Project Contract。接口类型、路径或
owner 字段一旦被模型改写，合同校验失败后会重复生成大对象；Provider 传输中断还会被归为
普通 Agent Runtime 错误，最终拖到 Worker 硬截止。

### 解决

- 新增 `project_delivery_layered`，把 L0/L1/L2 结构化架构对象接入完整的任务、环境、代码、
  测试和审查 DAG。
- Planner 选择旧 `project_delivery` 且目标同时出现前后端、数据库、异步、并发或交互等
  复杂度信号时，控制面稳定升级到 `project_delivery_layered`。
- Contract 节点优先使用 `ArchitectureDesignBundle` 确定性编译合同，避免 Markdown 反向猜测。
- 为常见模型词汇增加边界归一化（例如 `repository`→`service`、`router`→`api`），未知值仍
  由合同校验拒绝。
- 新增 `provider_transport` 和 `provider_terminal_missing` 失败类型，Provider 连接断开或
  缺少终态时执行独立、有界重试。

### 验证

复杂度升级、Provider 传输归因和分层交付模板测试通过；全量测试共 328 项通过。

## 31. 2026-08-30：架构工具误报与文件粒度收敛

### 真实复现

FHL 的分层交付 Trace `tr-e4902cf31bba` 在流式架构蓝图节点未收到可确认的 Provider 终态，
最终触发 900 秒 Worker 截止。关闭流式后，Trace `tr-a8c13bd14624` 能完成需求、蓝图和模块
设计，但 `architecture_agent` 将本地 `write_implementation_design` 工具误报为缺失能力；
控制面找不到动态来源，错误进入能力审批死路。

### 修复与边界

- Runner 将架构本地写入工具的误报转换为有界结构化重试，并在重试提示中明确当前 depth 对应
  的工具；外部文档等真实动态能力仍按审批流程处理。
- `ArchitectureArtifactWorkflow` 在 DTO 边界把多文件实现单元拆成“一单元一 owned_file”，
  避免 Code 合同因文件粒度不一致而拒绝整个架构输出。
- BaseAgent 的文本化工具回放白名单补齐架构结构化工具，兼容不保留原生 tool-call 的网关。

### 验证结论

控制面测试为 329 passed、3 skipped；`scripts/validate_layered_instance.py` 的确定性分层架构
最小闭环通过。真实 FHL 流程仍未进入 Code/Test/Review：剩余风险是 Provider 模型未稳定选择
已注册的架构工具，不能把该次运行标记为完整交付。

## 32. 2026-08-30：架构工具最小暴露与结构化诊断

### 问题

分层 ArchitectureAgent 原先在 `partitioned` 模式可以同时看到三个结构化写入工具、读取工具
和旧 Markdown 暂存工具。工具执行失败返回自然语言后，模型容易把参数校验错误误报为能力缺失，
让一个本地 DTO 错误进入外部能力审批路径。

### 设计调整

- 控制面按 WorkItem slot 设置最小工具白名单：blueprint 只允许
  `write_architecture_blueprint`，module-* 只允许 `write_module_design`，implementation-* 只允许
  `write_implementation_design`，分层 integration 只允许 `integrate_architecture_designs`；每条
  路线仍保留必要的 `load_architecture_input`。
- 旧 `architecture_parallel`、`architecture_compact` 等 Markdown 路线不受新白名单影响，避免改变
  既有兼容流程。
- 架构 DTO 校验错误统一返回 `error_type`、`retryable`、`expected_tool` 和字段路径；模型和 Runner
  可以区分 schema 错误、depth 错误与真正的动态能力请求。
- `implementation-*` 和 `module-*` slot 按前缀解析字符限制，避免回退到错误的默认上限。

### 验证

新增工具白名单、结构化错误和前缀限制测试；架构、Runner 与合同测试通过。该设计将工具选择从
模型决策收回控制面，模型只负责生成当前对象的参数，降低架构阶段进入错误审批路径的概率。

## 33. 2026-08-31：工具层协议语义对齐

### 真实问题

工具来源此前统一声明 `execute() -> str`，但成功文本、`{"ok": false}` 的领域校验响应和异常
没有统一语义。于是带 `completion_policy=final` 的工具可能把失败 JSON 当作终态；文本化工具
调用在写入失败后仍可能因尾部“已完成”被 Agent 判为成功；MCP schema 或本地参数错误也可能
被模型改写成 `capability_request`，进入不存在动态来源的审批死路。

### 修复

- 在 `ToolSource` 边界增加 `ToolResult` 和 `ToolResultStatus`（completed/failed/retryable/
  blocked），保留领域服务原有字符串接口，由工具层统一解释结果。
- 增加 `ToolExecutionError`，并按 `tool_validation`、`input_missing`、`tool_transport`、
  `tool_authorization`、`tool_execution` 分类；本地工具错误不再隐式等价于外部能力缺失。
- 所有本地、受信执行和 MCP 来源统一写入 `ok/status/error_type/retryable` 记忆元数据；
  `execute_safe()` 提供不抛异常的控制面调用入口。
- CrewAI 适配器在任何工具返回 `ok=false` 时抛出结构化工具错误，`final` 仅对成功调用生效。
  文本化本地工具回放若执行失败则向 Runner 传播，不再被自然语言尾句掩盖。
- BaseAgent 会把模型声称缺失、但当前已暴露的本地工具转换为 `tool_protocol` 重试信号；
  Runner 不再需要把这类文本猜测当作动态能力请求。
- `ToolDef` 注册时校验名称、参数根 schema、required 字段和执行模式；MCP 动态声明缺失或
  非法 schema 会在发现边界直接拒绝。

### 验证

新增工具定义语义校验、机器可读失败结果、`execute_safe` 分类和终态阻断测试；控制面测试
`339 passed, 3 skipped`。这次改动不改变领域服务或 DAG 结构，只收紧工具协议边界。

## 34. 2026-08-31：分层架构交付状态与合同引用修复

### 真实问题

真实 FHL 全流程暴露了四个运行时边界：编译后的 `wi-XX-` 节点 ID 未被集成完成门识别；
蓝图模块使用 `todo-api` 而分区对象使用 `api`；模型偶尔声明未分配的
`verification/quality` 模块；合同接口 `kind` 使用 `provided/consumed` 等方向或同义词；
实现合同把完整 `staged:` 引用误当作 artifact key；环境已由控制面准备后，bootstrap 的
重复能力请求会错误结束整条 DAG。

### 修复

- 集成节点和恢复校验按语义后缀识别编译 ID，并强制唯一架构候选后才允许完成。
- 架构集成对唯一的模块 ID 后缀做确定性归一化；蓝图阶段校验模块集合必须与已分配分区一致。
- 合同 `InterfaceContract.kind` 归一化常见方向词和同义词，未知值仍拒绝。
- 实现计划编译器解析 `published:`、`staged:` 引用，避免受限 ID 错误。
- 控制面自动满足本地 bootstrap 能力请求时只完成当前节点并继续调度下游节点。

### 验证

控制面测试 `340 passed, 3 skipped`。真实 trace `tr-430a6d4b8b78` 已完整通过需求、三层架构并行、
架构集成/质量门、Project Contract、任务集成/质量门和环境准备，并展开 6 个并行代码单元；
随后因 `runtime-server-composition` 连续两次在 Provider 宽限期内无流式进度而停止，checkpoint
恢复保持正确，代码/测试/review 尚未形成最终交付。该剩余问题属于 Provider/模型停滞边界，不是工具协议或状态语义误判。

## 35. 2026-09-02：恢复原始八阶段路线并收口语义注册

### 背景

前几轮重构一度把“动态架构稳定性”单独当成新阶段，导致路线图与最初约定的八阶段发生偏差。
复盘后确认：稳定性、观测、Provider 终态和失败归因都属于阶段三至阶段七的横向验收条件，
不能替代 ContractCompiler、下游交付节点和多项目 E2E 的阶段性交付。因此恢复原始路线：
固定模板与 ProcessDefinition、Blueprint 语义校验、动态 ModuleDesign、动态 ImplementationDesign、
ContractCompiler、Tasks/Environment/Test/Review、多项目 E2E，最后才删除固定模板。

### 本轮改动

- 新增 `SemanticRegistry`，集中登记字段的 `meaning/source/consumer/value_rules`，并提供节点局部投影。
  `compile_node_contract` 改为从 Registry 编译语义合同，仍保持 canonical 字段和既有任务输入对象不变。
- `BlueprintValidator` 在规模、依赖和环检查前先执行 Registry 语义校验；层依赖、模块 purpose、
  module_id 唯一性和未知依赖现在会以统一的 `Blueprint 语义校验失败` 诊断返回。
- Registry 允许控制面注入自定义节点字段，但拒绝覆盖公共字段或重复注册，避免项目扩展重新引入同义字段。
- 动态架构的 L0/L1/L2 结构化对象、工具回放、接口 ownership、文件级 unit 和 wave 约束保持硬校验；
  语义 Registry 只补充解释和关系校验，不放宽 DTO 或权限边界。

### 阶段验收状态

- 阶段一已完成；阶段二的集中注册入口已补齐。
- 阶段三、阶段四已通过动态计划扩展和真实 FHL 架构探针，模块数量和实现层级由 Blueprint/ModuleDesign
  决定，而不是由模板预先写死。
- 阶段五已落地：`ArchitectureDesignBundle -> ProjectContract -> ImplementationContractCompiler`
  可以确定性生成单文件、按 wave 排序的 CodeAgent WorkItem；正在补充真实 Provider 连续验收。
- 阶段六的模板依赖已经接入动态代码扩展：Tasks、Environment 作为每个代码 unit 的前置，随后经过
  Integration、Test、Review；仍需完整真实运行证明这些节点在 Provider 停滞和 checkpoint 恢复后能继续闭环。
- 阶段七尚未完成，至少需要覆盖简单 Python、前后端+数据库、异步/并发和依赖审批四类项目，并记录
  节点覆盖率、产物完整率、重试/恢复次数、Review 状态和重复运行稳定性。
- 阶段八暂不启动。`project_delivery`、`project_delivery_layered` 及旧架构模板继续作为兼容/回退适配器，
  只有阶段六、七的实测证据稳定后才删除。

### 验证

- 控制面回归：`tests` 目录 `395 passed, 3 skipped`；加入 SemanticRegistry、动态尾部和集成误报回归后仍全部通过。
- `compileall` 和 `git diff --check` 通过。
- 真实 FHL Trace `tr-430a6d4b8b78` 的最新可确认进度仍是架构、合同、任务和环境完成，6 个代码 unit 已展开；
  Provider 在 `runtime-server-composition` 阶段无流式进度后由宽限/ checkpoint 机制停止，未伪造 Code/Test/Review 完成。
  这次结果把剩余问题明确收敛到 Provider/Agent 终止协议与真实 E2E 验收，不再归因于字段语义或总体 DAG 设计。

### 下一步

先用确定性 Agent 完成阶段五到阶段六的完整计划回归，检查动态代码 unit 与 Tasks/Environment/Integration/Test/Review
的依赖、输入引用和产物 owner；再在真实 FHL 上做阶段七矩阵验证。固定模板删除必须等矩阵连续通过后再决定。

## 36. 2026-09-03：真实 FHL 集成误报的闭环修复

### 复现

真实 FHL 架构探针 `tr-a2722f1916a3` 已完成 Blueprint、4 个 ModuleDesign 和 4 个
ImplementationDesign，随后在 Architecture Integration 返回：
`{"type":"capability_request","capability":"integrate_architecture_designs",...}`。
该工具其实是 Architecture domain 已注册的本地确定性工具，但 Runner 的误报识别只覆盖了
三个 `write_*` 工具，没有覆盖集成工具，于是把本地协议错误当成外部能力请求，最终以
`没有可提供能力 'integrate_architecture_designs' 的来源` 阻塞。

### 修复

- 将 `integrate_architecture_designs` 纳入 Architecture 本地工具误报识别集合。
- 对 Architecture Integration 的误报增加控制面兜底：使用当前 WorkItem 的授权上下文调用
  `ArchitectureArtifactWorkflow.integrate_structured_designs`，重新读取全部 staged L0/L1/L2
  对象并执行同一 `ArchitectureDesignBundle` 硬校验后创建候选；校验失败仍返回
  `needs_replan`，不会绕过合同或伪造完成。
- 记录 `architecture_integration_control_plane_fallback` 事件，使 Provider 是否发出原生
  tool-call 与控制面最终采用的执行路径可审计。
- 新增回归测试，证明本地集成工具误报不会进入能力审批死路；动态三层测试使用真实 staged
  对象验证兜底可以发布候选。

### 验证与阶段影响

- 控制面测试更新为 `395 passed, 3 skipped`（含新增集成误报、动态尾部和语义 Registry 回归）。
- `scripts/validate_layered_instance.py` 完成 10 个架构/合同节点，状态 `completed`。
- 该修复完成阶段五的一个真实 Provider 边界，并为阶段六的下游 Tasks、Environment、Code、
  Integration、Test、Review 连续执行消除一个确定性阻塞点。阶段七仍需在真实 FHL 上验证完整
  交付和多项目矩阵；如果 Provider 在设计节点本身断流，仍按 `provider_transport`/
  `provider_terminal_missing` 或 watchdog 规则处理。

## 37. 2026-09-07：层依赖校验下沉到 pydantic 边界

### 暴露的问题

真实全链运行在 Architecture Blueprint 节点完成后立即失败,报错:
```
架构 Blueprint 无法扩展模块计划: Blueprint 语义校验失败: 
层 presentation 依赖未声明层: Python 标准库; 
层 application 依赖未声明层: Python 标准库; 
层 data 依赖未声明层: Python 标准库
```

LLM 在 `LayerDecision.allowed_dependencies` 里填入了 `["application", "Python 标准库"]`。
按字段语义,`allowed_dependencies` 的值域只能是"已声明的架构层名",但 LLM 自然地把语言/
标准库也当成一种"依赖"塞进去——这是个**高频、可预期的范畴错误**。

真正的卡点不是 LLM 犯错,而是**校验时机错位 + 无恢复通道**:

1. **第一道校验**(pydantic `ArchitectureBlueprint.validate_unique_ids`): 只查"层名不重复、
   文件路径合法",**不查依赖引用合法性** → 通过 ✅
2. 节点标记 `completed` ✅
3. **第二道校验**(控制面扩展时 `SemanticRegistry.validate_blueprint`): 这里才查
   "依赖必须引用已声明的层" → 失败 ❌
4. 控制面返回错误字符串 → GraphRunner 直接 `return FAILED` → **无重试、无反馈给 LLM、
   无修复计划**

对比:Blueprint 的 **schema 校验**失败有**有界重试**,会把字段级错误反馈给 Agent 重写。
但这个**关系校验**在"节点已完成之后"才跑,走的是完全不同的代码路径,没有回流通道。

### 系统级修复

**下沉自包含校验到 pydantic 边界**

"层依赖必须引用已声明的层"是**自包含校验**(所有信息都在一个 `ArchitectureBlueprint`
对象里),完全可以在 pydantic 模型解析时就拦住。下沉后它会变成和 schema 失败同一类的
`ARCHITECTURE_SCHEMA_VALIDATION`,自动走**有界重试 + 字段级反馈**。

1. 在 `ArchitectureBlueprint.validate_unique_ids` 里添加层依赖校验逻辑:
   - 收集所有已声明的层名 `known_layers`
   - 遍历每层的 `allowed_dependencies`,检查是否都在 `known_layers` 里
   - 检查层不能依赖自身
   - 失败时抛出 `ValueError`,包含精确错误信息和已声明层列表
2. 从 `SemanticRegistry.validate_blueprint` 移除冗余的层依赖校验(保留注释说明下沉原因)
3. 在 runner 的架构重试提示词 `_architecture_retry_contract_prompt` 里补充"不要把
   语言/标准库列为层"的明确指引

### 设计意义

这次修复闭合了一个**结构性设计缺口**:校验被分成"节点内"(pydantic,有重试)和"节点间"
(控制面扩展,无重试)两个阶段,而有些**逻辑上属于"单对象完整性"的校验却被放在了第二阶段**,
导致本来能自愈的常见错误掉进了"无恢复通道"的路径。

下沉后的边界更清晰:
- **第一层**(pydantic):单对象的结构不变式 + 自包含引用完整性 → 失败=节点未成功,
  走有界重试
- **第二层**(语义校验):真正跨对象的关系约束(模块依赖图、接口 owner 匹配) → 
  失败应转成 NEEDS_REPLAN 或接入 repair(当前已部分支持,未来可继续完善)

### 验证

- 新增 `BlueprintLayerDependencyValidationTest` 测试套件,覆盖:
  - 层依赖引用不存在的层时 pydantic 解析失败
  - 层依赖引用已声明的层时解析成功  
  - 层不能依赖自身
- 修复 `test_field_semantics.py` 中受影响的测试(改为测试模块依赖校验)
- 控制面完整回归:`395 passed, 3 skipped`

### 后续影响

这次下沉是 `field-semantics-refactor` 分支的一个里程碑:把"在哪校验、失败怎么恢复"
的规则从隐式约定变成了显式边界。后续可以继续识别其他"自包含但被误放在第二层"的校验,
逐步让校验层次和恢复策略一致。真正跨对象的校验失败,也应该建立统一的 `FailureKind`
并接入现有 `plan_repair` 机制,而不是直接判死。
  `provider_terminal_missing` 记录并从 checkpoint 恢复。

## 37. 2026-09-03：动态计划接入稳定交付尾部

### 问题

阶段三、四的动态架构只负责根据 Blueprint/ModuleDesign 生成项目专属的模块和实现设计节点。
如果 Planner 选择了动态架构路线但没有预先枚举 Tasks、Environment、Code Integration、Test、Review，
原 Runner 只在 `project_delivery`/`project_delivery_layered` 模板中展开 CodeAgent，动态计划会在合同
发布后停在架构里程碑，无法进入真实交付。

### 设计与实现

- 新增 `DynamicDeliveryTailBuilder`。它以 `ProcessDefinition` 的生命周期角色为依据，在 Project Contract
  完成且动态计划明确包含交付意图时，追加受控的 Tasks（分区/集成/质量门）、Environment、Code Integration、
  Test 和 Review 节点。
- 追加节点只引用当前计划中的 Requirement、Architecture、Contract 和发布产物；不包含任何项目模块、文件
  数量或技术栈假设。原 Planner 可能生成的宽泛下游占位节点会在未执行前被确定性替换，避免重复写入或权限重叠。
- ContractCompiler 随后把唯一 Project Contract 展开为单文件、按 wave 排序的 CodeAgent WorkItem，所有代码
  unit 继承动态尾部的合同/任务/环境前置；集成节点的输入引用由真实 ChangeSet 重新绑定。
- 固定模板仍保持原有行为，作为兼容和回退适配器；动态尾部是新增路径，不改变现有模板的节点 ID 或授权。

### 验证

- 动态合同计划（`template_id=None`）可以展开为两个并行代码 unit，且 Code Integration 依赖被正确重写。
- 动态尾部单元测试验证了 7 个受控节点、任务/环境/审查输入引用和占位替换行为。
- 控制面回归应保持所有 `tests` 目录测试通过；生成项目的测试仍需在项目自身工作目录运行，避免根目录收集历史项目的同名模块。

### 阶段状态

这一步完成了阶段五到阶段六的控制面衔接：动态架构不再依赖固定项目模板才能进入代码和验证阶段。
阶段七仍以真实 FHL 多项目 E2E 为准，重点观察 Provider 长耗时、代码 ChangeSet 完整率、Sandbox 证据和最终
Review 是否连续闭环；阶段八删除固定模板继续冻结。

## 38. 2026-09-03：ImplementationDesign 动态预算修复

### 复现

阶段七的真实 FHL 运行在前端模块的 `ImplementationDesign` 节点失败，错误表现为实现设计响应约
6.5k 字符却被 `artifact_budget` 拒绝。旧规则使用 `5000 + 1500 * (unit_count - 1)` 的总上限：
它没有为接口、测试类型、依赖和 requirement 元数据预留稳定开销，两个文件级 unit 的合法设计很容易
在写入前被截断。该失败发生在架构产物预算门，不是字段 schema、工具授权或 Provider 连接故障。

### 修复

- 总预算改为“固定 envelope + 每 unit 规划额度 + 产品最低线”的有界公式：固定预留 2500 字符，
  每个 unit 3500 字符，最低不低于 5000；第三个 unit 之后仍至少每个增加 1000 字符。
- 总上限提升到 24000 字符，避免多 unit 模块在元数据齐全时被过早拒绝；单个完整文件 unit 继续
  硬限制 4500 字符，防止单个 unit 吞噬整个 envelope。
- `llm_token_budget_for_design` 继续从动态字符预算推导 token 上限，最多 24000；部署环境仍可
  通过显式 `PROJECTOS_LLM_MAX_TOKENS` 覆盖。

### 验证与影响

架构合同和 CrewAI 适配回归通过。该修复只改变实现设计节点的容量计算，不改变 DAG、对象字段、
工具白名单或授权范围；下一次真实 FHL 测试应重点确认前端/运行时多 unit 设计可以继续进入
Architecture Integration，而不是在预算门提前失败。

## 39. 2026-09-03：真实架构集成的接口别名与 Wave 归一化

### 复现

放宽观测阈值后，真实 FHL 运行已经完成 Requirement、Blueprint、三个 ModuleDesign 和三个
ImplementationDesign，但 Architecture Integration 仍被两个模型表达差异阻塞：
`domain.todo_operations` 与提供方的 `domain.todo_task_operations`、以及
`api.todo_request_handler` 与 `api.todo_http` 实际指向同一依赖边界；同时，domain 模块的
两个 unit 被模型放在同一 wave，却声明了前后依赖，触发“依赖必须更早 wave”的硬校验。

### 修复

- Integration 以 ModuleDesign 的接口目录和模块依赖为 canonical source。对同一依赖命名空间下
  唯一提供接口的描述性别名做确定性归一化；存在多个候选或无关命名时仍拒绝，避免模糊合并。
- Integration 在 DTO 硬校验前传播 unit wave：依赖 unit 自动提升到提供方 wave+1；未知 unit 或
  循环依赖不做修正，继续由硬校验报告。

### 真实验证

针对失败 Trace `tr-ff47b2fcabc0` 的七个真实 staged 架构对象重新执行
`integrate_structured_designs`，已成功创建唯一架构候选 `cand-23670a5e2ae0`，证明别名和 wave
归一化可以被真实产物消费。该操作未绕过 DTO、文件 ownership 或权限校验；候选创建后的完整
下游交付仍需新的 Trace 验证，避免手工候选影响历史运行状态。

## 40. 2026-09-03：目录授权排除与环境能力同义词收敛

### 复现

真实 Trace 在架构合同编译为 CodeAgent WorkItem 时失败：实现单元声明
`allowed_paths=["backend/app/**"]`，同时用 `forbidden_paths` 排除入口或其他受控文件。
旧校验只要两个模式前缀相同就报告“重叠”，把合法的目录授权加文件排除误判为权限冲突。
恢复后 BootstrapAgent 又将本地的 `prepare_environment`/`save_environment` 概括为
`environment_preparation_and_persistence`，能力归一化遗漏该表达，错误进入外部来源查找。

### 修复

- WorkItem 路径硬门改为拒绝“禁止范围覆盖整个授权范围”（相同文件、相同递归 glob 或更宽的
  递归目录）；目录内的明确文件/子模式排除允许存在，并在写入时继续由 Git Gateway 拒绝命中路径。
- `owned_files` 命中禁止范围仍在合同构造阶段拒绝；全局 `*`/`**` 禁止模式和授权越界规则保持不变。
- Agent capability 归一化把包含 environment + prepare/save/persistence 的复合表达统一为
  `environment_preparation`，由控制面已有环境准备和保存逻辑直接处理，不请求不存在的 MCP 来源。

### 验证

- 路径合同回归覆盖：目录内文件排除可用、完整覆盖仍拒绝、owned file 命中排除仍拒绝。
- 相关回归测试 `54 passed`；真实 FHL Trace 已从架构 checkpoint 恢复并完成 Tasks，下一次恢复将
  继续验证环境、Code、Integration、Test 和 Review 的连续闭环。

## 41. 2026-09-03：FHL 动态交付尾部真实闭环复测

### 运行范围

使用真实 FHL `gpt-5.6-terra` 和 Docker sandbox 恢复 Trace
`tr-260a2bed5b5d`（项目 `todo-fhl-budget-20260903c`）。本次没有替换 Agent 为
Fake 实现，也没有跳过控制面节点；恢复从架构合同 checkpoint 继续，完整经过：

```text
Requirement → Architecture Blueprint/ModuleDesign/ImplementationDesign
→ Architecture Integration → Quality Gate → Project Contract
→ Tasks → Environment → 3 个分层 CodeAgent → Code Integration
→ Test → Review
```

### 结果

- Trace 状态：`completed`。
- 三个代码 unit 均产生真实 ChangeSet，Integration 完成并发布实现摘要。
- Docker sandbox 的 `unit` 证据 `ev-80308e21ac14` 为 `passed`，实际输出为 `Ran 2 tests`、`OK`。
- `review.md` 已落盘，Review 结论为 `PASS`；`project.quality.v1` 报告 `issues=0`。
- 受控 Test/Review 兜底正确处理了模型把本地工具描述成缺失能力的情况，没有进入无意义的 MCP 审批死路。

### 真实启动探针暴露的边界

对生成项目直接执行合同声明的 `python -m backend.app.server` 后，入口因
`api.py` 没有提供 `create_application`、`create_app` 或 `handle` 而退出。也就是说，
当前 TestAgent 的固定 `unit` 检查能够证明导入和领域 smoke 行为，却不能证明 HTTP 服务已经
真正组装并监听端口；Review 的确定性结构策略同样不会替模型补写缺失的运行时适配。

这不是本次 Trace 的控制面失败：需求只声明了标准库 REST 适配边界，模型产物在既有测试覆盖下
被判定为合格，但它揭示了阶段七必须补充的质量门：对合同声明的 backend entrypoint 执行受信
runtime smoke（至少验证入口可组装并能响应 health/readiness），并将失败证据标记为
`runtime_preflight`/`SANDBOX_SETUP`，禁止生成 PASS Review。

### 控制面回归

- `.venv/bin/pytest -q tests`：`407 passed, 3 skipped`。
- `compileall` 和 `git diff --check` 通过。
- 旧测试 `test_test_agent_cannot_complete_without_sandbox_evidence` 已按当前语义改为验证
  可恢复的 `BLOCKED`，并确认前置检查失败时不会重复调用 Agent。

### 阶段影响

本次完成阶段五到阶段六在真实 Provider 上的连续验证，并为阶段七提供第一条完整 Trace 证据。
阶段七尚未完成：除了当前标准库 Todo 形态，还需要至少一个前后端/数据库或异步项目，并将
runtime smoke、跨层接口可达性和业务 API 行为纳入验证矩阵；阶段八删除固定模板继续冻结。

## 42. 2026-09-03：运行入口组装静态质量门

### 触发原因

`tr-260a2bed5b5d` 的生成项目通过了模块导入和领域 smoke，但直接执行合同声明的
`python -m backend.app.server` 时才发现 `api.py` 没有
`create_application`、`create_app` 或 `handle`。这说明“可导入”与“可启动”是两个不同的
交付事实，不能只依赖固定 unittest discovery。

### 改动

- `ProjectRuntimePreflight` 新增 `runtime.application_assembly_missing` 检查：当合同声明的
  Python 入口包含动态 `_load_application` 组装逻辑时，静态解析相邻 `api.py` 是否提供一个
  受约定的应用组装函数。
- 检查使用 Python AST，不在宿主机执行生成代码；入口缺失或 API 组装函数缺失会在 TestAgent
  前返回 `RUNTIME_PREFLIGHT` 阻塞，后续可从 checkpoint 修复，不会伪造测试或 Review 通过。
- `RuntimeCatalog`/`SandboxPolicy` 增加受信 `runtime-smoke` 检查；当合同声明 backend 入口时，
  TestAgent 会在 Docker sandbox 内导入该模块，并优先实例化 `create_server(..., 0)` 或校验
  ASGI `app`，从执行层再次验证应用组装，而不是只依赖静态分析。
- 增加正/反例回归测试；现有项目合同、FastAPI 结构检查和 Sandbox 行为保持不变。

### 验证

- 当前生成项目重新执行前置检查得到：
  `runtime.application_assembly_missing`（与手工启动探针一致）。
- 直接执行该项目的 `runtime-smoke` 时，当前主机 Docker credential helper 返回 `(-50)`，
  因而得到 `setup_failed`；这次环境故障没有覆盖此前已通过的 `unit` sandbox 证据，也没有被
  误判为应用代码失败。
- `.venv/bin/pytest -q tests`：`410 passed, 3 skipped`。
- `compileall` 与 `git diff --check` 通过。

### 后续边界

静态门与 `runtime-smoke` 组合可以确认入口可导入/组装，但尚不能证明 HTTP 路由、健康检查和
真实端口监听可用。阶段七仍需把合同的 `health_path` 纳入受信启动探测，并把响应级证据纳入
Review；历史 Trace 的 PASS 结论不会被事后重写，后续新运行会在 Test 阶段应用这些检查。

## 43. 2026-09-03：结构化执行进度与逐节点恢复参考

### 背景

旧 Worker 快照使用单一 `phase/last_progress_at`：LLM chunk、Agent 摘要、工具执行和落盘事实
会互相刷新同一个时钟。并行批次又只把最后写入事件投影到顶层，快速完成的兄弟节点可能掩盖
另一个已停滞的节点。恢复时只能重放宽泛 Prompt，缺少受限、可核验的当前工作状态。

### 收敛结果

- 编排层统一维护 `lifecycle/activity/outcome` 三轴状态，并将事件分为
  `transport/semantic/control/heartbeat` 四类信号。LLM chunk 不再刷新业务进展时钟。
- 所有领域获得受限 `report_progress` 工具，只允许短摘要、下一步和当前 WorkItem 已授权引用；
  重复摘要不会制造假活跃，也不能宣告节点完成。
- Worker 快照保留每个 WorkItem 的独立状态、时钟和有限事件历史；watchdog 为每个活动节点
  建立独立宽限窗口，区分 Provider 传输停滞与语义停滞。
- `WorkingState` 只记录成功工具动作、staged/candidate/ChangeSet/Sandbox/正式产物引用、
  下一步和错误类型。重试 Prompt 明确把它标记为恢复参考，不回放模型正文或思维链。
- `/progress` 在兼容旧快照的同时提供 `overall_state`、活动/等待/完成/失败计数和 `current` 列表；
  Worker 终态优先于尚未解决节点的过程投影。
- 工具和 Sandbox 的旧空闲环境变量继续兼容，新部署可按 activity 使用
  `PROJECTOS_SEMANTIC_STALL_*_SECONDS` 覆盖。

### 验证与边界

- 全量测试 `425 passed`，`compileall` 和 `git diff --check` 通过。
- 当前恢复仍以单机 Trace、checkpoint、ArtifactRepository 和 Git ChangeSet 为基础；没有跨主机
  队列租约。模型在首次持久化动作之前断线时，不存在可靠的内部生成续传，只能重试最小 WorkItem。

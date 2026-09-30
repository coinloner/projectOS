# ProjectOS 执行控制板

> 最后更新：2026-09-29
> 总目标：完成 ProjectOS 重构计划，并以可审计证据证明完整交付闭环。
> 当前模型：`wanfa / gpt-6-sol / responses`
> 当前 Live Trace：`tr-2e4924e985cf`
> 当前执行协议：目标持续、阶段闸门、短切片反馈。

## 当前状态

- 总体状态：`in_progress`
- 当前阶段：`Phase 3 Replay L1b 已验证；F 类生成项目实现/容器切片已实测通过；Phase 4 隔离故障注入已验证（9/9）；Phase 5 Live L0 新 Trace 结构流程 11/11 完成；F 版 Blueprint/Contract 候选 v2 已验证但未激活，canonical L0 交付闸门仍未通过；原 Trace 合同迁移未激活`
- 不允许的动作：直接启动完整 Live L2；创建新 Trace 冒充恢复；静默连续运行数小时。
- 下一步闸门：F 类实现已在隔离容器通过；Phase 4 的 Wave 冲突与 Runtime 进程启动失败已有隔离直接证据；Phase 5 Live L0 修正 preflight 后已运行，同一新 Trace 11/11 完成；但 Project Contract 缺启动入口与 required_files，无法放行 L1。已完成新 D 产物写入/合同保存门禁与本 Trace 的 F 版 Blueprint/Contract v2 候选；下一闸门须明确候选激活/重规划与 AC-10 文档 ownership 策略，不能用未激活候选宣布 Live L0 通过。不自动重跑 Provider 或进入 L1；原 Trace 合同迁移单独决策。

## 阶段状态

| 阶段 | 状态 | 完成证据 |
|---|---|---|
| Phase 0 基线/审计 | `substantially_verified` | `validation-evidence/l1-closure-audit-20260928.json`、已有 pytest/failure matrix 证据 |
| Phase 1 Replay L0 | `verified` | `validation-evidence/replay-l0-20260927.json`；trace `tr-0984a9da91c1`；10/10 nodes；semantic loop passed |
| Phase 2 Replay L1a | `verified` | `validation-evidence/replay-l1a-codeagent-20260927-corrected.json`；trace `tr-514221d4ae14`；生产 CodeAgent 1 次调用；ChangeSet `8ef7bdea31e876323e18edeebef9cbbabf753714`；Wave merge `fef29be26cd7d99c945979060e035ce564343636`；`/health=200`；`runtime_smoke_passed` |
| Phase 3 Replay L1b 业务切片 | `verified_replay_amended_contract` | `validation-evidence/replay-l1-business-20260928-065529.json`；trace `tr-03f323cea803`；9 单元完整合同编译后只执行 3 个后端 WorkItem；3 个 ChangeSet / 3 个 Wave；AST/binding/HTTP gate 通过；`POST /tasks=201`、空白 400、重启后 `GET /tasks=200`、SQLite 原始行仍在。另见同 Trace 的 `import-binding-gate-*`、`http-contract-gate-*`、`runtime-business-smoke-*`。**仅证明经过明确补全的 Replay 合同；原 Live 合同仍缺冻结路由/入口/binding（F），原 Trace 仍 failed。** |
| Phase 4 故障注入/恢复 | `verified_isolated_fault_injection` | 策略矩阵 7/7；Runner/Coordinator 直接边界案例累计 9/9（原 7 项 + Wave 冲突责任局部重调度 + 实际 Python 进程启动失败阻断 Review）；相关广度回归 91 passed / 9 subtests。非 Live Provider/容器运行成功证据。 |
| Phase 5 Live L0/L1 | `live_l0_candidate_v2_validated_inactive` | 新 Trace `tr-b23fd9b89e41`：preflight HTTP 200，11/11 WorkItems 完成、Contract 8 单元；但缺 backend_file/backend_import/backend_command/frontend_file 与 required_files，故 **Live L0 交付闸门未通过**。后验只读审计 `validation-evidence/live-l0-audit-tr-b23fd9b89e41.json`；原 Trace 仍 failed。 |
| Phase 6 Replay L2 | `verified_separately` | `validation-evidence/replay-l2-browser-full-20260927.json` |
| Phase 7 原 Live Trace 完整恢复 | `pending` | 只有原 Trace completed 才能完成 |

## Phase 5 Live L0 首轮闸门（2026-09-28）

- 获放行范围仅 Live L0。真实 `wanfa / gpt-6-sol / responses` 预检共 2 次：首次在约 20.4 秒超时（P）；第二次 HTTP 400，原始响应指出 `max_output_tokens=8` 低于该端点最低 16（R，`app/llm/preflight.py`）。没有进行第三次请求。
- **未创建项目/Trace，未执行 Requirement/Architecture/Contract**；因此 Live L0 状态不是通过，也不是 Provider 不支持该模型的结论。原 Live Trace 不变。证据：`validation-evidence/live-l0-preflight-20260928.json`。
- 另一个入口缺口：现有 `scripts/validate_wanfa_architecture.py` 使用的 `architecture_only` 模板只到架构质量门，未包含 Contract 节点；不能以该脚本的完成声称 Requirement → Architecture → Contract 的 Live L0。下一切片须先修预检 token 下限，并选择/实现真正 L0 工作流，经本地回归后再决定是否发起有界 Live 重试。当前已超过本轮承诺的观察窗口，不继续调用。

## Phase 5 Live L0 第二切片（2026-09-28，进行中）

- R 修复：`app/llm/preflight.py` 将 Responses `max_output_tokens` 从 8 改为 16；本地 `54 passed / 9 subtests`，日志 `validation-evidence/live-l0-local-entry-tests-20260928.log`。
- R 修复：新增受控 `architecture_l0` 模板，在架构质量门后增加确定性 Contract WorkItem，且无 Code/Test/Review；旧 `architecture_only` 不变。验证脚本改用新模板，完成时必须审计 Project Contract、digest、checkpoint 和发布事件。
- 当前新 Trace：`tr-b23fd9b89e41`，项目 `projects/wanfa-architecture-protocol-20260928-224311-319836`，preflight HTTP 200/terminal/tool schema 已通过，Requirement、Blueprint、`task_management` 模块已完成；当前执行 `http_service` 模块。原始事件与 checkpoint 在该项目 `.projectos/runs/tr-b23fd9b89e41/`；当前结论仅 `running`，不能写成 Live L0 完成。

## Phase 5 Live L0 第三切片（2026-09-28，同 Trace 收口）

- 只观察 `tr-b23fd9b89e41` 原运行会话，未创建新 Trace 或调用 Live L1。真实运行 `trace.json=completed`、11/11 WorkItem 与 checkpoint 完成；架构候选 7 个源、Contract 8 单元、可编译出 8 个未执行 CodeAgent WorkItem。
- 启动脚本的首版审计错误要求 `architecture_contract_published` 出自 Contract WorkItem，而该遗留事件由 Architecture Quality Gate 发出；Contract 正确事件是 `deterministic_action_completed`。已修 V 类断言，保留原 `validation-evidence.json` 的 exception 和 `validation-evidence/live-l0-run-20260928.log`，没有抹去失败记录。
- 再次严格审计发现 F/R 缺口：Project Contract 的 `backend_file`、`backend_import`、`backend_command`、`frontend_file` 均为空，`required_files=[]`；尽管 API operations 已冻结，合同仍不足以保证后续启动/交付。第一次过宽后验审计保存在 `validation-evidence/live-l0-preliminary-audit-tr-b23fd9b89e41.json`，修正后的 fail-closed 证据为 `validation-evidence/live-l0-audit-tr-b23fd9b89e41.json`，结果 `blocked_f_contract`。本地相关回归 `96 passed / 9 subtests`（`validation-evidence/live-l0-post-audit-tests-20260928.log`）。
- **决策闸门**：不把结构性完成写作 Live L0 验收通过，也不启动 Live L1。需决定是否对该新 Trace 的合同生成版本化修订候选，或先收紧 Blueprint/Contract 质量门再获放行重新产生合同；原 Live Trace `tr-2e4924e985cf` 仍 failed，未改。


## Phase 5 Live L0 修订第一切片（2026-09-29，已停在激活闸门）

- 获放行范围：只修订现有新 Trace `tr-b23fd9b89e41` 的 L0 交付候选并收紧后续新 D 产物质量门；不触碰 canonical 合同、已完成 plan/checkpoint/Trace，不进入 Live L1。
- `R`：`app/domain/architecture/handoff.py` 新增 Blueprint 生产者、整合 Bundle 和 semantic Project Contract 写入的 fail-closed 检查；启动入口与文件名、命令导入目标、实现 ownership / 提供符号和项目级 required_files 不一致即拒绝。旧持久化对象仍可读、用于诊断。分区提示明确区分架构自行选择文件与用户原始需求，不再把运行入口和必交付文件说成可选。
- `F`：从当前 Live 的 staged Blueprint、3 ModuleDesign、3 ImplementationDesign 和 canonical 合同派生**未激活** v2 修订候选；Blueprint 冻结 `backend/http_service.py`、`backend.http_service:app`、拟议 uvicorn 命令、`frontend/index.html`，给 3 模块分配原 11 个已 owned 的必需文件；Project Contract 确定性重新编译后仅 `entrypoints` 和 `required_files` 两字段不同，8/8 Code WorkItem 可编译但没有运行。第一版候选曾额外收紧 unit.required_paths，已保留证据但由更小且来源一致的 v2 取代。
- `V`：`validation-evidence/live-l0-f-handoff-v2-20260929.json` 含全部源引用/digest、候选 digest `421649e5c87e83a28bf514abd8eb2a7d4883760b02841c8b5024c64ddca6c1f2`、原合同/Trace/plan/checkpoint 等 SHA；`validation-evidence/live-l0-post-policy-audit-20260929.json` 仍正确报告 canonical `blocked_f_contract`。相关回归 `124 passed / 9 subtests`，日志 `validation-evidence/live-l0-handoff-gate-tests-20260929.log`；`git diff --check`、compileall 通过。
- 未解决：拟议启动命令未经过 Live 运行时验证；源 AC-10 的启动访问说明未有单独 owned 文档文件；v2 Blueprint/Contract 均未发布或重绑定到持久化 plan/checkpoint。**不能宣布 Live L0 或 L1 通过**。原 Live Trace `tr-2e4924e985cf` 仍 failed。下一切片需要用户明确放行对 v2 候选的显式激活/重规划方案；不得静默覆盖任何 authoritative digest。
- 时间反馈：00:59～02:00 CST，约 61 分钟，超过承诺的 20 分钟上限；中途已主动告知，后续应进一步缩短单切片并按时停闸。

## Phase 4 当前切片（2026-09-28，故障注入与局部恢复）

- 修复 `R`：明确 502/503 的 OpenAI-compatible `Error code: NNN` 归类为 `provider_transport`，避免误计入通用 `agent_runtime`；每个责任 CodeAgent WorkItem 实测最多两次调用（首次 + 一次重试）。
- 修复 `R`：失败的 forced-rerun 仍保留恢复标记与诊断；只有新的完成结果才清除，以免 Worker 恢复时复用旧 staged ChangeSet。
- 新隔离验证 7/7：403 不重试；502/503 各一次重试；terminal event 缺失不完成；无 ChangeSet 三次有界尝试后不完成；import binding 与 HTTP contract 使用真实语义校验诊断，经 Coordinator 决定 owner，再实际重新调度责任 WorkItem；已完成 sibling 保留，受影响后代失效、Runtime 不启动。后两个场景在 integration NodeResult 边界注入语义诊断，在 owner 重进边界注入停止结果，**不代表修复代码已经成功提交**。
- `V` 证据：`validation-evidence/phase4-direct-20260928.json`，各行含独立 Trace ID、原始 `events.jsonl` / `checkpoint.json` 路径、before/after frontier 和入口事件；`validation-evidence/phase4-closure-focused-20260928.log`（8 passed）；`validation-evidence/phase4-broad-regression-20260928.log`（90 passed / 9 subtests）。原策略矩阵 `validation-evidence/failure-matrix-phase4-20260928.json` 仍仅是策略级证据，不再用其推算值冒充实际调度。
- **本次收口**：Wave 0 模拟 Git 三方冲突发生在下一批调度前：`wi-downstream` 未启动，仅 `wi-owner` 被加入 forced rerun，`wi-sibling` 保留；实际 Python 子进程在静态 preflight 通过后非零退出，stderr/exit code 作为 SandboxEvidence 持久化，即使 TestAgent 文本宣称完成，Runner 仍以 `sandbox_setup` 阻断 Review。新增证据 `validation-evidence/phase4-closure-20260928.json`（2/2，内含两个 Trace 的 events/checkpoint 指针）和 `validation-evidence/phase4-broad-regression-closure-20260928.log`（91 passed / 9 subtests）。这是隔离注入，不是 Live Provider 或 Docker 容器启动成功。Phase 4 标记 `verified_isolated_fault_injection`；Phase 5 未放行。原 Live Trace `tr-2e4924e985cf` 不变、仍 `failed`。

## F 类实现修复切片（2026-09-28，后端/前端/容器运行配置）

- 已修改生成项目 workspace，不修改 ProjectOS 控制面或原 Live Trace：Repository 优先读取 `TASK_DB_PATH`；补充 backend requirements；统一 Dockerfile、Compose 的 `/workspace/backend`、`app.main:app`、依赖安装、`/data/tasks.db` 持久卷；补充前端 Vite/React build、Nginx 静态产物和 `/tasks`/`/health` 代理配置。
- 检查结果：HTTP 重启持久化测试 `1 passed`；`TASK_DB_PATH` 独立路径测试通过；Compose 合同静态检查通过；前端 `npm run build` 通过（Vite 8.3.1，生成 `frontend/dist`）；Docker Compose config 结果见 `validation-evidence/f-compose-config-20260928.yaml`（若本机 Docker 可用）。
- 证据：`validation-evidence/f-implementation-backend-tests-20260928.log`、`validation-evidence/f-implementation-frontend-build-20260928.log`、`validation-evidence/f-implementation-slice-20260928.json`。
- Docker 容器运行时补充证据见 `validation-evidence/f-implementation-runtime-20260928.json`：前后端容器均 healthy；首页 200；Nginx 代理 `/health`=200、`GET /tasks`=200、`POST /tasks`=201、空标题=400；创建记录在 backend 容器 restart 后仍可经 frontend proxy 读取。临时 smoke 记录随后已删除，命名卷保留。
- 本切片关闭的是隔离生成项目中的 F 类实现/运行配置缺口；未调用真实 Wanfa、未执行浏览器 E2E，也未修改/迁移原 Live Trace 合同。不得据此宣称原 Trace 恢复或完整 Live 验证完成。

## Phase 3 指定复验（2026-09-28，v2 F 候选，隔离 Replay）

- 用户明确要求重跑；本轮只跑一次，使用只读 F 合同 v2 输入，Replay 副本仅清除 3 个选中单元的过期 Live staged refs/slot；没有覆盖先前 Replay 或原 Live Trace。
- 新 Replay Trace `tr-c046387e7d2a` 为 `completed`。9/9 单元编译，选中 3 个后端 CodeAgent、3 个 ChangeSet、3 个 Wave Git commit；AST import、canonical binding、HTTP contract、跨层代码检查通过。
- 运行时直接证据：`GET /health=200`，空标题 `POST /tasks=400`，创建 `POST /tasks=201`，进程重启后 `GET /tasks=200` 且 SQLite 原始行存在。
- 本次证据：`validation-evidence/replay-l1-business-f-candidate-20260928-075247-411229.json`、`validation-evidence/phase3-rerun-audit-tr-c046387e7d2a.json`、同 Trace 的 import/HTTP/runtime gate JSON、Replay 项目中的 events.jsonl、ChangeSet、Wave commits 和进程日志。原 2026-09-28 06:55:29 证据保留。
- **边界**：仍为记录的 Live 生成文件作为确定性模型响应，非真实 Wanfa 调用；仅验证 3 个后端单元，未验证 F 候选的 `TASK_DB_PATH` 容器持久卷、前端构建/代理、Compose/浏览器。原 Live Trace `tr-2e4924e985cf` 仍 `failed`，原合同/Trace/plan/checkpoint 四个哈希未变。不得将此次 Replay 成功解释为 F 全部修复或 Live 恢复。

## F 类设计修订切片（2026-09-28，未激活）

- 原 Live 合同 digest `70953d6408bb740f4d3c5226f532ed3295893da821e3be9ac4c28875d1d5a2ea`，原 Trace `tr-2e4924e985cf` 仍 `failed`；**不能直接覆盖 canonical 合同**，因为持久化 plan/WorkItem 依赖旧 digest。
- 版本化修订候选 v2：`projects/live-l2-20260926-rerun/wanfa-live-l2-todo-20260927/.projectos/architecture/revisions/project-contract-f-correction-v2-20260928.json`；digest `4d8d5958040d7f3e702e459aaf1ff05c46981cb111418b62b7f05878418f98f5`。v1 候选及历史证据保留，不覆盖。
- 实质设计修改：冻结六条 `/tasks` method/path、`GET /health` 独立接口、`TaskRepository → TaskService → app` 跨层绑定、后端 `app.main:app` 入口和 `TASK_DB_PATH` 持久卷约束；同一 9 单元合同的 `task-web-entry` 现显式拥有前端依赖清单/Vite 配置并要求编译 JSX，运行时约束要求同源 `/tasks` 代理。
- 直接证据：`validation-evidence/f-design-correction-v2-20260928.json`、`validation-evidence/f-design-correction-v2-audit-20260928.json`、`validation-evidence/f-design-correction-v2-tests-20260928.log`；9/9 编译、46 个相关 pytest 通过、候选 digest 重算一致、原合同/Trace/plan/checkpoint SHA-256 未变。仅静态/合同审计；没有运行 Live Provider、容器或浏览器。
- F 类实现修复现已在隔离生成项目中完成并通过容器运行时 smoke：Repository 支持 `TASK_DB_PATH`；Dockerfile/Compose 后端依赖、入口、工作目录和数据库卷一致；前端 Vite 构建、Nginx 静态服务及 `/tasks`、`/health` 同源代理均已运行验证。详见 `validation-evidence/f-implementation-runtime-20260928.json`。
- V 类已知诊断：通用 HTTP gate 把测试文件中的 `GET /health` 错归 `task_management.task_api`；健康接口和后端路由存在，审计保留误报，未宣称 gate 零诊断。
- 原 Live Trace 激活闸门仍未通过：须先确定显式重规划/合同 digest 与已完成 WorkItem 的兼容策略；不得静默迁移 checkpoint、plan 或基线。真实 Wanfa 与后续 Phase 仍需单独放行。

## Phase 3 留存的设计缺口

- 原 Live 合同的 task API method/path、运行入口与 Service/Repository canonical binding 未冻结；Phase 3 仅在隔离 Replay 副本补全这些字段，未修改原 Live 合同或原 Trace（F）。
- 本次只覆盖任务创建/查询的业务切片；HTTP contract gate 证明 provider 声明的 POST/GET /tasks，运行时客户端确实调用相同路径，**不证明未执行的浏览器 consumer**。
- Replay 使用已记录的三个 Live 代码文件作为确定性 Provider 响应；不代表真实 Wanfa 调用成功，也不代表原 Live Trace 完成。
- 阶段索引：`validation-evidence/evidence-index-20260928.json`，当前仍是 partial/in_progress。

## 协作规则

### 助手每次开始前

必须读取：

1. 本控制板；
2. `docs/execution-plan-2026-09-27-v2.md`；
3. 当前阶段对应的最新证据；
4. 原 Live Trace 的当前权威状态。

### 助手每次只做一个阶段切片

每个切片必须声明：

- 本切片目标；
- 最长观察时间；
- 成功条件；
- 失败停止条件；
- 预计修改文件；
- 是否允许进入下一阶段。

阶段结束后必须停在检查点，报告实际结果。除非用户明确说“继续下一阶段”，否则不跨越阶段闸门。

### 用户只需要做两类决策

1. **策略决策**：接受/拒绝某个修复方向、是否允许扩大范围；
2. **阶段放行**：`继续 Phase N`。

用户不需要批准普通命令、测试和只读检查。

### 失败分类

所有发现必须标记为以下之一：

- `R`：ProjectOS 重构/控制面问题；
- `F`：生成项目/原始设计/业务合同问题；
- `P`：Provider 或外部环境问题；
- `V`：验证脚本或证据问题。

### 反馈格式

每次阶段反馈固定包含：

```text
阶段：
结果：通过 / 失败可归因 / 外部阻塞
耗时：
修改文件：
命令与结果：
证据路径：
问题归因：R / F / P / V
是否形成量化成功：
下一步：
```

## 用户可用的短指令

- `继续 Phase 1`：只执行 Phase 1，完成后停止。
- `继续下一阶段`：放行当前阶段之后的一个阶段，不跨两阶段。
- `只验证不修改`：禁止代码修改，只收集证据。
- `只修 R 类问题`：禁止修改生成项目业务代码。
- `只修 F 类问题`：禁止修改 ProjectOS 控制面。
- `暂停并汇报`：立即停止当前切片，保留当前证据。
- `按控制板恢复`：重新读取本文件和最新权威状态后继续，不依赖聊天记忆。

## 完成定义

只有以下条件全部满足，控制板才能改为 `completed`：

- Replay L0、Replay L1a、Replay L1b 通过；
- Live L0、Live L1a、Live L1b 通过；
- Replay L2 证据完整；
- 原 Live Trace `tr-2e4924e985cf` 完成；
- Tests、Review、Runtime、API E2E、Browser E2E 均有直接证据；
- 失败历史和最终 evidence index 均保留。

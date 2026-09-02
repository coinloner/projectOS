## 2026-08-21

- 明确运行时只有两条路径：受控 Workflow，或动态 Planner 生成 DAG。
- 移除未参与正式执行路径的 `requirement_generation` 模板，保留 `requirement_agent`。
- 增加能力审批查询和批准后恢复接口：`GET .../capabilities`、`POST .../capabilities/approve`。
- 增加 Docker runtime preflight，提前报告 `python:3.12-slim` 镜像和依赖缓存前置。
- Sandbox setup failure 会保留证据并将 Trace 标记为 BLOCKED；修复 runtime/sandbox 后从 checkpoint
  恢复测试，避免把未执行的测试伪装成完成并继续生成误导性的 Review。
- CLI 改为显式 `--project --goal [--workflow]`，移除硬编码项目入口。

## 2026-08-22

- CrewAI LLM 默认开启 `stream=True`，通过事件总线记录调用开始、SSE chunk、完成和失败。
- 新增 Worker 进度快照 `.projectos/runs/<trace_id>/worker-status.json`，不保存 token 正文，只保留阶段、时间戳和计数器。
- Worker 监管从单一硬超时升级为硬截止 + LLM/工具/Sandbox 阶段空闲阈值 + 宽限期；新增 `worker_idle_suspected` 和 `worker_idle_timeout` 事件。
- `ExecutionContext.progress` 贯穿 Agent 和受控工具，API 增加运行进度字段和 `/progress` 查询端点。
- 全量 208 个单测在当前受限环境中有 12 个环境型错误（共享端口耗尽、Socket bind 权限、MCP demo 服务未启动）；核心变更相关测试和编译检查通过。详见 [EVOLUTION](EVOLUTION.md)。
- 电商平台压力测试 `commerce_platform_e2e` 生成了完整 9 节点计划，需求、架构、任务分区/集成、环境、实现、测试和 Review 均实际执行。实现阶段首轮未写摘要，受控重试后生成后端领域/模型/schema/端口文件和 `implementation.md`；测试阶段先因依赖审批产生 `setup_failed`，批准依赖后复测通过（53 tests，2 skipped），并触发 Repair Plan。最终 `review.md` 正确判定 `BLOCKED`：缺少 API/controller、application service、infrastructure repository、前端、迁移和 `tests.md`，质量门阻止了不完整项目被误报为完成。该结果证明失败证据、修复重规划、复测和最终审查链路已闭环，但也说明复杂项目需要让 CodeAgent 继续覆盖完整架构，而不是只生成领域骨架。

## 2026-08-25

- 记录并固化此前五项系统级重构：持久化且可撤销的 `CapabilityGrant` 授权生命周期；携带 `repair_scope` 的局部重规划与 checkpoint 恢复；由需求显式声明驱动的外部文档依赖；`allowed_roots` 驱动的 CodeAgent 工程文件范围；以及共享端口生命周期的 development/production 运行模式。
- 完成四层收敛：工具合同层增加 `ToolDef.completion_policy`；执行适配层统一映射 CrewAI `result_as_answer`；Worker 状态层采用阶段空闲阈值、Provider 宽限期和硬截止；配置与验证层统一终态工具标记、环境变量和回归文档。
- 修复工具调用不收尾：保存架构等产物成功后，CrewAI 原先仍会继续推理并重复请求模型，严重时因空响应触发 `Invalid response from LLM call - None or empty.`。产物提交工具使用 `final`，代码读写工具使用 `continue`。
- 修复 Provider 停滞误判：停滞只先记录诊断，使用 `PROJECTOS_PROVIDER_STALL_GRACE_SECONDS` 作为唯一宽限期；真实 `last_progress_at` 变化会清零停滞计时，heartbeat 不作为业务进度依据。
- 更新演化复盘、编排和工具管理文档，删除旧的 `PROJECTOS_IDLE_GRACE_SECONDS` 语义。全量回归为 `247 tests, 2 skipped`，编译和差异检查通过。
- 新增 `DeliveryContract`，为 requirement、architecture、environment、implementation、tests 和 review
  文档声明唯一 owner 与阶段边界；编译器和实现合同展开时校验 owner 节点存在，防止节点要求未来阶段产物。
- 新增 `ProjectRuntimePreflight`，在 TestAgent 运行前检查 Compose Dockerfile、数据库初始化入口以及前端
  HTML 的本地引用；缺失入口直接产生结构化 `runtime_preflight` 阻塞，不再等最终 Review 才发现。
- Integration Review 的 findings 支持 `blocker/error/warning/info` 严重级别。LLM 只能记录风险，确定性
  Git Policy 仍是合并底线；运行完整性问题交由运行前置检查和测试阶段验证。
- sandbox `setup_failed` 不再转换为 completed；原始 SandboxEvidence 持久化后返回 `SANDBOX_SETUP`，
  Runner 将 Trace 标记为 BLOCKED，恢复动作明确为修复环境后从 checkpoint 继续。
- 库存平台 `inventory_delivery_full_v2` 全链路实测已进入 8 路并行 CodeAgent 和 Integration Review。
  依赖审批按摘要恢复，冷启动依赖下载超时改为可配置（默认 600 秒）；集成阶段真实发现迁移入口和领域
  模块缺口并拒绝合并，未伪造 tests/review 产物。另修复无尾斜杠目录合同被误判为缺失文件的问题。
## 2026-08-26 CodeAgent 落盘可靠性

- 写入工具现在记录 `file_write_started`、`file_write_succeeded`、`changeset_created` 进度事实，并保留有限事件历史。
- `code_delivery_incomplete` 使用独立重试预算；重试提示要求先写入最小文件骨架，再补充实现。
- 交付门增加空文件、Python AST 语法和后端入口 `app` 符号检查。
- 代码文件仍必须通过 ChangeSet、路径所有权和 Integration 检查，模型自然语言不能代替落盘事实。

### 文件级职责与合同 ID 修复

- 真实活动报名平台 Trace `tr-71898419e4db` 在架构合同保存后暴露：LLM 将 `scripts/**` 等目录 glob
  放入 `owned_files`，编译器直接把路径拼入 WorkItem ID，Artifact ID 校验失败并使 Worker 在进入
  Code Wave 前崩溃。
- 编译器现在对由完整文件拆分生成的 ID 做安全化，只保留受限 ID 字符集；目录 glob 仍保留在路径
  授权中，不会被误当成单个文件。
- 多文件实现单元的 objective 现在按文件职责收窄。后端 `main.py` 明确只负责组合根、路由注册、
  异常处理和 `/health`，禁止把业务用例、数据库查询或 schema 塞进入口文件。
- 对组合根读取前置 ChangeSet 时使用 AST 导入/顶层符号摘要，避免把所有实现正文重复塞进单次提示，
  降低模型长时间阅读后未调用 `write_staged_code_file` 的概率；其他代码文件仍可读取完整内容。
- 回归验证：`277 tests, 2 skipped`，`compileall` 和 `git diff --check` 通过。真实 Trace 随后因
  Docker 依赖解析在受限环境中失败（网络重试与容器空间不足）而阻塞，未伪造代码、测试或 Review 产物。

### 依赖环境自愈

- `DockerDependencyResolver` 增加结构化失败分类、三阶段恢复和有限 stderr；每次尝试使用新容器。
- 默认先解析 binary wheel，存储失败后改用项目私有宿主临时目录，网络失败后扩大连接重试和超时。
- 最终失败会清理当前依赖摘要的半成品缓存；成功缓存仍由 digest 和 `.projectos-ready` 双重约束。
- `EnvironmentPreparation` 持久化 `failure_kind`、`attempts` 和 `recovery_actions`，供 API、Trace 和恢复流程诊断。
- 自愈不会越过依赖审批，也不会执行影响其他项目的全局 Docker 清理。

### 全链路验证：`inventory_contract_e2e_20260826_v2`

- 依赖审批后，环境节点由 `dependency_failed` 自动恢复为 `ready`，生成了按 requirements digest
  隔离的 wheel cache；没有手工创建或重建项目容器。
- 真实代码 Wave 依次完成 domain、application、migrations、infrastructure 和 API 分区，目录 glob
  所有权门不再误报缺失文件。
- Wave 3 Integration 按真实 ChangeSet 阻塞：API 分区引用了应用层和基础设施层尚未实际落盘的模块。
- 该结果是有效质量门证据，不是环境故障，也没有生成伪造的 tests/review 产物。下一步应对
  application/infrastructure 分区执行局部修复，再恢复 Integration。

# 2026-09-02

- 第二阶段架构重构：流程阶段仍由 `ProcessDefinition` 固定，Blueprint 的模块 `purpose` 和
  `depends_on_modules` 成为业务语义事实；控制面新增 `DynamicPlanBuilder`，在 Blueprint
  完成后动态生成 depth=1 ModuleDesign WorkItem，按依赖计算并行 wave，并持久化扩展 provenance。
- Planner 草案新增可选 `stage_id`。它只表达流程语义，执行模式、slot、发布目标和输出类型仍由
  `PlanValidator` 硬编码编译，避免模型借字段注入权限。旧模板和旧 Trace 保持可恢复。
- 第三阶段架构重构：ModuleDesign 全部完成后，`DynamicPlanBuilder.expand_implementations()`
  按模块一对一生成 depth=2 `ImplementationDesign` WorkItem。实现设计节点只依赖本模块的
  ModuleDesign，直接依赖模块通过冻结 staged 引用传递，避免同级实现设计形成隐式顺序。
- Runner 在完成检查前依次执行 Blueprint 扩展和 ImplementationDesign 扩展；架构集成节点会被
  自动补齐实现设计依赖和输入引用，只有三层对象齐备后才能生成 ArchitectureDesignBundle。
- 动态扩展 provenance 现在按 `kind` 保存不可覆盖的记录，同时保留最新扩展指针；checkpoint、
  plan.json 和 Trace 事件都记录新增 WorkItem，Worker 重启后沿用同一份 DAG。
- 第三阶段回归：控制面测试 `378 passed, 3 skipped`，`tests/test_dynamic_builder.py` 覆盖动态实现
  节点、模块依赖和 Runner 三层最小闭环；`compileall` 通过。

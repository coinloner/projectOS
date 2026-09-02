# 流程定义与项目计划

ProjectOS 将软件交付流程和项目执行图分开维护。

`ProcessDefinition` 只描述稳定的生命周期阶段、合法转换、阶段输入/输出类型和全局规模上限。
它不包含具体项目模块、文件路径或并行分支。当前内置流程为 `software_delivery`，阶段包括需求、
架构蓝图、模块设计、实现设计、架构集成、合同、任务、环境、实现、代码集成、测试和审查。

`WorkflowTemplate` 仍作为现有受控 DAG 的兼容适配器，带有 `process_id` 归属。模板编译出的
`ExecutionPlan` 同时持久化 `process_id` 和历史 `template_id`，因此旧 Trace 可以恢复，而后续
`DynamicPlanBuilder` 可以基于同一流程规则生成项目专属节点。

第二阶段已经启用 Blueprint 到 ModuleDesign 的动态扩展：Blueprint 中每个模块必须携带业务
`purpose` 和 `depends_on_modules`。Blueprint 通过确定性校验后，控制面才会生成对应的模块
WorkItem；无依赖模块并行，有依赖模块按 wave 等待前置模块。扩展会写入
`.projectos/runs/<trace_id>/expansions/<plan_id>.json`，记录 Blueprint、模块节点和依赖波次，
从而支持重启后的审计和恢复。

第三阶段已启用 ModuleDesign 到 ImplementationDesign 的动态扩展：所有模块设计完成后，
控制面为每个模块生成一个 depth=2 实现设计 WorkItem。该节点只依赖自己的 ModuleDesign，
通过冻结 staged 引用读取 Blueprint 和直接依赖模块的语义；架构集成会等待全部实现设计，
再确定性组成 `ArchitectureDesignBundle`。实现设计完成后仍由现有 Project Contract 编译器
拆分为文件级 CodeAgent WorkItem，代码执行路径没有被架构扩展逻辑替换。

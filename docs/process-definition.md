# 流程定义与项目计划

ProjectOS 将软件交付流程和项目执行图分开维护。

`ProcessDefinition` 只描述稳定的生命周期阶段、合法转换、阶段输入/输出类型和全局规模上限。
它不包含具体项目模块、文件路径或并行分支。当前内置流程为 `software_delivery`，阶段包括需求、
架构蓝图、模块设计、实现设计、架构集成、合同、任务、环境、实现、代码集成、测试和审查。

`WorkflowTemplate` 仍作为现有受控 DAG 的兼容适配器，带有 `process_id` 归属。模板编译出的
`ExecutionPlan` 同时持久化 `process_id` 和历史 `template_id`，因此旧 Trace 可以恢复，而后续
`DynamicPlanBuilder` 可以基于同一流程规则生成项目专属节点。

当前阶段不会改变旧模板的节点数量或执行逻辑。下一阶段会在 Blueprint 完成后读取其真实的
`layers`/`modules`，按流程定义动态扩展 ModuleDesign 和 ImplementationDesign 节点，并为每次
扩展生成新的计划版本。

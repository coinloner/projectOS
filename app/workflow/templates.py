from app.execution_context import ExecutionMode
from app.workflow.template import TaskBlueprint, WorkflowTemplate

def project_delivery_template() -> WorkflowTemplate:
    """从需求澄清到代码、测试和审查的默认交付流程经验。"""
    return WorkflowTemplate(
        id="project_delivery",
        name="项目交付草案",
        description="生成需求、架构合同、实施任务、并行代码分区、测试证据和审查报告。",
        nodes=(
            TaskBlueprint(
                id="requirement",
                agent_id="requirement_agent",
                objective="将用户目标整理为结构化需求文档并保存到项目目录。",
                output_key="requirement",
            ),
            TaskBlueprint(
                id="architecture",
                agent_id="architecture_agent",
                objective="根据需求设计系统架构并保存架构文档。",
                output_key="architecture",
                depends_on=("requirement",),
            ),
            TaskBlueprint(
                id="architecture-contract",
                agent_id="architecture_contract_agent",
                objective="读取已发布架构并保存可校验的 Implementation Contract，供后续代码分区执行。",
                output_key="architecture_contract",
                depends_on=("architecture",),
                acceptance_criteria=(
                    "合同包含实现单元、分层、允许路径、依赖和验收标准。",
                    "允许路径不能重叠，依赖图必须无环，不能编造需求之外的功能。",
                ),
            ),
            TaskBlueprint(
                id="tasks-plan",
                agent_id="task_agent",
                objective="根据需求和架构交付可验收的实施任务决策包。",
                output_key="tasks_plan",
                artifact_key="tasks",
                depends_on=("requirement", "architecture", "architecture-contract"),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="plan",
                input_artifacts=("requirement", "architecture", "architecture_contract"),
                acceptance_criteria=(
                    "使用简体中文；只列 MVP 必需任务，最多 8 项。",
                    "每项包含依赖、产出和可验证验收条件。",
                ),
            ),
            TaskBlueprint(
                id="tasks-integration",
                agent_id="task_agent",
                objective="整合任务决策包并生成紧凑的 tasks 候选。",
                output_key="tasks_candidate",
                artifact_key="tasks",
                depends_on=("tasks-plan",),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="tasks",
                input_from=("tasks-plan",),
                acceptance_criteria=(
                    "保留任务依赖、产出和验收条件；不得加入未被需求支持的功能。",
                ),
            ),
            TaskBlueprint(
                id="tasks-quality-gate",
                agent_id="task_agent",
                objective="检查任务候选并发布通过质量门的 tasks.md。",
                output_key="tasks_published",
                artifact_key="tasks",
                depends_on=("tasks-integration",),
                execution_mode=ExecutionMode.QUALITY_GATE,
                publish_target="tasks",
                candidate_from="tasks-integration",
            ),
            TaskBlueprint(
                id="environment",
                agent_id="bootstrap_agent",
                objective="根据架构和任务声明项目 runtime、依赖意图和 sandbox 环境报告。",
                output_key="environment",
                depends_on=("requirement", "architecture", "architecture-contract", "tasks-quality-gate"),
                input_artifacts=("requirement", "architecture", "architecture_contract", "tasks"),
            ),
            # CodeAgent 不再由模板预置一个“总实现”节点。架构合同发布后，
            # GraphRunner 会把实现单元编译成一个或多个 PARTITIONED WorkItem。
            TaskBlueprint(
                id="code-integration",
                agent_id="code_integration_agent",
                objective="将所有代码分区的 ChangeSet 按策略合并到正式 workspace。",
                output_key="implementation_merge",
                artifact_key="implementation",
                depends_on=("architecture-contract", "tasks-quality-gate", "environment"),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="workspace",
            ),
            TaskBlueprint(
                id="tests",
                agent_id="test_agent",
                objective="为 workspace 中的实现编写并运行基础测试，保存测试报告。",
                output_key="tests",
                depends_on=("requirement", "tasks-quality-gate", "environment", "code-integration"),
            ),
            TaskBlueprint(
                id="review",
                agent_id="review_agent",
                objective="审查需求、实现和测试证据，保存交付审查报告。",
                output_key="review",
                depends_on=("requirement", "architecture", "architecture-contract", "tasks-quality-gate", "environment", "code-integration", "tests"),
            ),
        ),
    )


def project_delivery_layered_template() -> WorkflowTemplate:
    """复杂项目的完整交付流程，使用三层结构化架构作为前置屏障。

    需求、架构和合同仍是同一条交付 DAG；与 ``project_delivery`` 的差别仅在
    架构阶段改为 L0/L1/L2 对象协议，Contract 节点由控制面确定性编译。
    """
    layered = architecture_layered_template()
    base = project_delivery_template()
    requirement_node = next(node for node in base.nodes if node.id == "requirement")
    layered_nodes = list(layered.nodes)
    blueprint = layered_nodes[0]
    layered_nodes[0] = TaskBlueprint(
        id=blueprint.id,
        agent_id=blueprint.agent_id,
        objective=blueprint.objective,
        output_key=blueprint.output_key,
        artifact_key=blueprint.artifact_key,
        depends_on=("requirement",),
        execution_mode=blueprint.execution_mode,
        input_artifacts=blueprint.input_artifacts,
        output_slot=blueprint.output_slot,
        publish_target=blueprint.publish_target,
        input_from=blueprint.input_from,
        acceptance_criteria=blueprint.acceptance_criteria,
        constraints=blueprint.constraints,
        non_goals=blueprint.non_goals,
        policy_id=blueprint.policy_id,
        policy_refs=blueprint.policy_refs,
        skill_refs=blueprint.skill_refs,
    )
    replacement = {
        "architecture": "architecture-layered-quality-gate",
        "architecture-contract": "architecture-layered-contract",
    }
    downstream: list[TaskBlueprint] = []
    for node in base.nodes:
        if node.id in {"requirement", "architecture", "architecture-contract"}:
            continue
        depends_on = tuple(
            replacement.get(dep, dep) for dep in node.depends_on
        )
        input_from = tuple(
            replacement.get(ref, ref) for ref in node.input_from
        )
        input_artifacts = node.input_artifacts
        if node.id == "tasks-plan":
            depends_on = ("requirement", "architecture-layered-quality-gate", "architecture-layered-contract")
        elif node.id == "environment":
            depends_on = ("requirement", "architecture-layered-quality-gate", "architecture-layered-contract", "tasks-quality-gate")
        elif node.id == "code-integration":
            depends_on = ("architecture-layered-contract", "tasks-quality-gate", "environment")
        elif node.id == "review":
            depends_on = ("requirement", "architecture-layered-quality-gate", "architecture-layered-contract", "tasks-quality-gate", "environment", "code-integration", "tests")
        downstream.append(
            TaskBlueprint(
                id=node.id,
                agent_id=node.agent_id,
                objective=node.objective,
                output_key=node.output_key,
                artifact_key=node.artifact_key,
                depends_on=depends_on,
                execution_mode=node.execution_mode,
                input_artifacts=input_artifacts,
                output_slot=node.output_slot,
                publish_target=node.publish_target,
                candidate_from=(replacement.get(node.candidate_from, node.candidate_from) if node.candidate_from else None),
                input_from=input_from,
                acceptance_criteria=node.acceptance_criteria,
                constraints=node.constraints,
                non_goals=node.non_goals,
                policy_id=node.policy_id,
                policy_refs=node.policy_refs,
                skill_refs=node.skill_refs,
            )
        )
    return WorkflowTemplate(
        id="project_delivery_layered",
        name="分层项目交付",
        description="复杂项目使用三层结构化架构合同，再进入任务、代码、测试和审查闭环。",
        nodes=(requirement_node,) + tuple(layered_nodes) + tuple(downstream),
    )


def architecture_parallel_template() -> WorkflowTemplate:
    """架构 Markdown 的受控并行试点模板。

    Planner 只选择该模板；其执行模式、slot、输入来源和质量门关系由模板固定。
    """
    return WorkflowTemplate(
        id="architecture_parallel",
        name="并行架构设计",
        description="先冻结基线，再并行设计架构分区，最后整合并通过质量门发布。",
        nodes=(
            TaskBlueprint(
                id="architecture-baseline",
                agent_id="architecture_agent",
                objective="根据已发布需求建立架构基线和统一边界。",
                output_key="architecture_baseline",
                artifact_key="architecture",
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="baseline",
                input_artifacts=("requirement",),
                acceptance_criteria=(
                    "只输出不超过 3200 字符的架构基线，不写实现细节。",
                    "固定包含：## System Boundary、## Shared Decisions、## Constraints、## Open Questions。",
                    "Shared Decisions 最多 6 条，作为后续分区唯一共享约束。",
                ),
            ),
            TaskBlueprint(
                id="architecture-api",
                agent_id="architecture_agent",
                objective="在架构基线约束下交付 API 与服务接口决策包。",
                output_key="architecture_api",
                artifact_key="architecture",
                depends_on=("architecture-baseline",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="api",
                input_from=("architecture-baseline",),
                acceptance_criteria=(
                    "只输出不超过 4200 字符的 API 决策包，不描述前端或存储实现。",
                    "固定包含：## Endpoints、## Request Response Contracts、## Validation Errors、## Dependencies。",
                    "Endpoints 最多 8 个；每项只保留方法、路径、输入、输出和错误语义。",
                ),
            ),
            TaskBlueprint(
                id="architecture-data",
                agent_id="architecture_agent",
                objective="在架构基线约束下交付数据模型与持久化决策包。",
                output_key="architecture_data",
                artifact_key="architecture",
                depends_on=("architecture-baseline",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="data",
                input_from=("architecture-baseline",),
                acceptance_criteria=(
                    "只输出不超过 4200 字符的数据决策包，不重写 HTTP 或页面设计。",
                    "固定包含：## Entities、## State Rules、## Persistence、## Data Ownership。",
                    "只列 MVP 必要实体、字段和状态规则；不展开迁移、索引或未来功能。",
                ),
            ),
            TaskBlueprint(
                id="architecture-frontend",
                agent_id="architecture_agent",
                objective="在架构基线约束下交付前端边界与交互决策包。",
                output_key="architecture_frontend",
                artifact_key="architecture",
                depends_on=("architecture-baseline",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="frontend",
                input_from=("architecture-baseline",),
                acceptance_criteria=(
                    "只输出不超过 4200 字符的前端决策包，不重复 API 或数据字段定义。",
                    "固定包含：## Views States、## API Usage、## Client Validation、## Boundaries。",
                    "只描述 MVP 页面状态、调用关系与错误呈现，不写组件代码。",
                ),
            ),
            TaskBlueprint(
                id="architecture-integration",
                agent_id="architecture_agent",
                objective="整合三个决策包，解决跨分区冲突并生成紧凑架构候选。",
                output_key="architecture_candidate",
                artifact_key="architecture",
                depends_on=("architecture-api", "architecture-data", "architecture-frontend"),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture",
                input_from=("architecture-api", "architecture-data", "architecture-frontend"),
                acceptance_criteria=(
                    "先列出 ## Integration Decisions，逐项说明 API、数据、前端间的冲突或确认结果。",
                    "再输出 ## Final Architecture，固定包含模块边界、核心实体、接口摘要、关键数据流和风险。",
                    "完整候选不超过 8500 字符；不得复制三个输入决策包的原文。",
                ),
            ),
            TaskBlueprint(
                id="architecture-quality-gate",
                agent_id="architecture_agent",
                objective="检查架构集成报告并发布通过质量门的候选。",
                output_key="architecture_published",
                artifact_key="architecture",
                depends_on=("architecture-integration",),
                execution_mode=ExecutionMode.QUALITY_GATE,
                publish_target="architecture",
                candidate_from="architecture-integration",
            ),
        ),
    )


def architecture_layered_template() -> WorkflowTemplate:
    """三层结构化架构流程。

    L0 只生成总体蓝图；L1 按模块并行；L2 将模块决策细化为可实现边界，
    最后由 Integration 统一校验并生成架构候选。深度由模板固定为 0/1/2。
    """
    module_nodes = (
        ("domain", "领域模块", "module-domain"),
        ("api", "接口模块", "module-api"),
        ("runtime", "运行模块", "module-runtime"),
    )
    nodes: list[TaskBlueprint] = [
        TaskBlueprint(
            id="architecture-blueprint",
            agent_id="architecture_agent",
            objective="根据需求产出 depth=0 的总体架构蓝图对象。",
            output_key="architecture_blueprint",
            artifact_key="architecture",
            execution_mode=ExecutionMode.PARTITIONED,
            output_slot="blueprint",
            input_artifacts=("requirement",),
            acceptance_criteria=(
                "只能调用 write_architecture_blueprint 保存结构化对象。",
                "depth 必须为 0，包含系统边界、层级、模块清单和全局约束。",
                "本模板的模块清单只能使用 module_id=domain、api、runtime；不得新增 verification、文档或其他未分配模块。",
            ),
        )
    ]
    for module_id, label, slot in module_nodes:
        nodes.append(
            TaskBlueprint(
                id=f"architecture-{slot}",
                agent_id="architecture_agent",
                objective=f"根据总体蓝图细化{label}，产出 depth=1 的 ModuleDesign 对象。",
                output_key=f"architecture_{slot}",
                artifact_key="architecture",
                depends_on=("architecture-blueprint",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot=slot,
                input_from=("architecture-blueprint",),
                acceptance_criteria=(
                    "只能调用 write_module_design 保存一个结构化模块对象。",
                    f"module_id 必须为 {module_id}，depth 必须为 1，并引用总体蓝图 design_id。",
                ),
            )
        )
    for module_id, label, slot in module_nodes:
        nodes.append(
            TaskBlueprint(
                id=f"architecture-implementation-{slot}",
                agent_id="architecture_agent",
                objective=f"将{label}细化为 depth=2 的实现准备对象。",
                output_key=f"architecture_implementation_{slot}",
                artifact_key="architecture",
                depends_on=(f"architecture-{slot}",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot=f"implementation-{slot}",
                input_from=(f"architecture-{slot}",),
                acceptance_criteria=(
                    "只能调用 write_implementation_design 保存结构化对象。",
                    f"module_id 必须为 {module_id}，实现单元必须声明具体 owned_files。",
                ),
            )
        )
    integration_inputs = tuple(node.id for node in nodes)
    integration_dependencies = integration_inputs
    nodes.extend(
        [
            TaskBlueprint(
                id="architecture-layered-integration",
                agent_id="architecture_agent",
                objective="整合 depth=0/1/2 架构对象，校验层级语义并生成架构候选。",
                output_key="architecture_layered_candidate",
                artifact_key="architecture",
                depends_on=integration_dependencies,
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture",
                input_from=integration_inputs,
                acceptance_criteria=(
                    "只能调用 integrate_architecture_designs。",
                    "不得重新设计模块；冲突必须通过结构化校验暴露。",
                ),
            ),
            TaskBlueprint(
                id="architecture-layered-quality-gate",
                agent_id="architecture_agent",
                objective="检查结构化架构候选并发布架构文档。",
                output_key="architecture_layered_published",
                artifact_key="architecture",
                depends_on=("architecture-layered-integration",),
                execution_mode=ExecutionMode.QUALITY_GATE,
                publish_target="architecture",
                candidate_from="architecture-layered-integration",
            ),
            TaskBlueprint(
                id="architecture-layered-contract",
                agent_id="architecture_contract_agent",
                objective="将已通过质量门的三层架构对象确定性编译为唯一 Project Contract。",
                output_key="architecture_contract",
                artifact_key="architecture_contract",
                depends_on=("architecture-layered-quality-gate",),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture_contract",
                input_from=integration_inputs,
                acceptance_criteria=(
                    "只能调用 compile_project_contract_from_designs。",
                    "不得重新解析 Markdown，不得新增架构对象或实现文件。",
                ),
            ),
        ]
    )
    return WorkflowTemplate(
        id="architecture_layered",
        name="三层结构化架构设计",
        description="总体蓝图、模块设计、实现准备三层架构对象并行与集成。",
        nodes=tuple(nodes),
    )


def architecture_compact_template() -> WorkflowTemplate:
    """简单需求的低成本架构流程：一个设计包、一轮规范化整合和质量门。"""
    return WorkflowTemplate(
        id="architecture_compact",
        name="精简架构设计",
        description="为范围明确的小需求生成短架构候选，避免不必要的并行分区。",
        nodes=(
            TaskBlueprint(
                id="architecture-design",
                agent_id="architecture_agent",
                objective="根据已发布需求交付一个紧凑、可实现的架构决策包。",
                output_key="architecture_design",
                artifact_key="architecture",
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="design",
                input_artifacts=("requirement",),
                acceptance_criteria=(
                    "只输出不超过 6000 字符的架构决策包。",
                    "固定包含：## System Boundary、## Modules、## Data Model、## API Summary、## Risks。",
                    "只覆盖需求中的 MVP；不列未来扩展、不写代码、不重复需求正文。",
                ),
            ),
            TaskBlueprint(
                id="architecture-integration",
                agent_id="architecture_agent",
                objective="将架构决策包规范化为可发布的紧凑架构候选。",
                output_key="architecture_candidate",
                artifact_key="architecture",
                depends_on=("architecture-design",),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="architecture",
                input_from=("architecture-design",),
                acceptance_criteria=(
                    "固定包含：## Architecture、## Modules、## Interfaces、## Data Rules、## Risks。",
                    "不超过 7500 字符；只消除歧义和重复，不新增未被需求支持的能力。",
                ),
            ),
            TaskBlueprint(
                id="architecture-quality-gate",
                agent_id="architecture_agent",
                objective="检查架构集成报告并发布通过质量门的候选。",
                output_key="architecture_published",
                artifact_key="architecture",
                depends_on=("architecture-integration",),
                execution_mode=ExecutionMode.QUALITY_GATE,
                publish_target="architecture",
                candidate_from="architecture-integration",
            ),
        ),
    )


def project_delivery_minimal_template() -> WorkflowTemplate:
    """从已有需求和架构进入代码、测试、审查的最小完整交付模板。"""
    return WorkflowTemplate(
        id="project_delivery_minimal",
        name="最小项目交付",
        description="使用已有需求和架构，生成任务、环境、并行代码、测试和审查结果。",
        nodes=(
            TaskBlueprint(
                id="tasks-plan",
                agent_id="task_agent",
                objective="根据中文需求和架构生成最小可验收任务决策包。",
                output_key="tasks_plan",
                artifact_key="tasks",
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="plan",
                input_artifacts=("requirement", "architecture"),
                acceptance_criteria=(
                    "使用简体中文；只列 MVP 必需任务，最多 8 项。",
                    "每项包含依赖、产出和可验证验收条件。",
                ),
            ),
            TaskBlueprint(
                id="tasks-integration",
                agent_id="task_agent",
                objective="整合任务决策包并生成紧凑的 tasks 候选。",
                output_key="tasks_candidate",
                artifact_key="tasks",
                depends_on=("tasks-plan",),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="tasks",
                input_from=("tasks-plan",),
                acceptance_criteria=(
                    "保留任务依赖、产出和验收条件；不得加入未被需求支持的功能。",
                ),
            ),
            TaskBlueprint(
                id="tasks-quality-gate",
                agent_id="task_agent",
                objective="检查任务候选并发布通过质量门的 tasks.md。",
                output_key="tasks_published",
                artifact_key="tasks",
                depends_on=("tasks-integration",),
                execution_mode=ExecutionMode.QUALITY_GATE,
                publish_target="tasks",
                candidate_from="tasks-integration",
            ),
            TaskBlueprint(
                id="environment",
                agent_id="bootstrap_agent",
                objective="为 MVP 选择可在 sandbox 中运行的最小 Python runtime。",
                output_key="environment",
                artifact_key="environment",
                depends_on=("tasks-quality-gate",),
                input_artifacts=("requirement", "architecture", "tasks"),
                acceptance_criteria=(
                    "使用简体中文；优先选择 python-stdlib，不能无必要引入外部依赖。",
                    "明确 runtime profile、测试命令和依赖状态。",
                ),
                skill_refs=("python.container-runtime.v1",),
            ),
            TaskBlueprint(
                id="code-backend",
                agent_id="code_agent",
                objective="实现 Todo MVP 的 Python HTTP backend 分区。",
                output_key="implementation_backend",
                artifact_key="implementation",
                depends_on=("environment",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="backend",
                input_artifacts=("architecture", "environment"),
                acceptance_criteria=(
                    "只写 backend/ 下的 Python 源码，至少提供可启动的 HTTP backend。",
                    "实现需求中的 Todo 创建、列表、完成/重新打开和删除能力。",
                    "使用 Python 标准库；不要写测试文件、README 或未来功能。",
                ),
                constraints=(
                    "技术栈固定为 Python 标准库；HTTP 接口和模块边界以 architecture 引用为准。",
                    "environment 引用只用于确认 runtime、依赖和启动/测试方式。",
                ),
                non_goals=(
                    "不修改 frontend/、workspace/tests/、README 或部署配置。",
                    "不重新设计总体架构，不实现需求之外的功能。",
                ),
                implementation_unit_id="backend",
                allowed_paths=("workspace/backend/**",),
                required_paths=("backend/main.py",),
                owned_files=("backend/main.py",),
                forbidden_paths=("workspace/frontend/**", "workspace/tests/**", ".projectos/**"),
                policy_refs=("project.layer-boundary.v1",),
                skill_refs=("python.http-service.v1",),
            ),
            TaskBlueprint(
                id="code-frontend",
                agent_id="code_agent",
                objective="实现 Todo MVP 的浏览器 frontend 分区。",
                output_key="implementation_frontend",
                artifact_key="implementation",
                depends_on=("environment",),
                execution_mode=ExecutionMode.PARTITIONED,
                output_slot="frontend",
                input_artifacts=("architecture", "environment"),
                acceptance_criteria=(
                    "只写 frontend/ 下的 HTML、CSS 和 JavaScript 文件。",
                    "页面必须能调用 backend API，支持创建、查看、完成/重新打开和删除。",
                    "不要写后端代码、测试文件、README 或未来功能。",
                ),
                constraints=(
                    "技术栈固定为 HTML、CSS 和原生 JavaScript；接口以 architecture 引用为准。",
                    "environment 引用只用于确认运行方式，不得引入未声明的构建工具。",
                ),
                non_goals=(
                    "不修改 backend/、workspace/tests/、README 或部署配置。",
                    "不重新设计总体架构，不实现需求之外的功能。",
                ),
                implementation_unit_id="frontend",
                allowed_paths=("workspace/frontend/**",),
                required_paths=("frontend/index.html",),
                owned_files=("frontend/index.html",),
                forbidden_paths=("workspace/backend/**", "workspace/tests/**", ".projectos/**"),
                policy_refs=("project.layer-boundary.v1",),
                skill_refs=("web.native-frontend.v1",),
            ),
            TaskBlueprint(
                id="code-integration",
                agent_id="code_integration_agent",
                objective="运行代码 Policy 并将 backend/frontend 暂存文件合并到正式 workspace。",
                output_key="implementation_merge",
                artifact_key="implementation",
                depends_on=("code-backend", "code-frontend"),
                execution_mode=ExecutionMode.INTEGRATION,
                publish_target="workspace",
                input_from=("code-backend", "code-frontend"),
                acceptance_criteria=(
                    "Policy 必须确认 backend 和 frontend 两个分区都有文件且没有路径冲突。",
                ),
            ),
            TaskBlueprint(
                id="tests",
                agent_id="test_agent",
                objective="为合并后的 MVP 编写最小 unittest 并在 Docker sandbox 中运行。",
                output_key="tests",
                artifact_key="tests",
                depends_on=("code-integration",),
                input_artifacts=("requirement", "tasks", "environment", "implementation"),
                acceptance_criteria=(
                    "使用简体中文报告；必须实际调用 run_sandbox_check。",
                    "不得将没有证据的测试声明为通过。",
                ),
            ),
            TaskBlueprint(
                id="review",
                agent_id="review_agent",
                objective="审查需求、架构、任务、代码、测试证据和运行环境，给出中文交付结论。",
                output_key="review",
                artifact_key="review",
                depends_on=("tests",),
                input_artifacts=(
                    "requirement", "architecture", "tasks", "environment",
                    "implementation", "tests",
                ),
                acceptance_criteria=(
                    "使用简体中文；区分已验证事实、风险和未覆盖项。",
                    "只能根据实际文件和 SandboxEvidence 下结论。",
                ),
            ),
        ),
    )

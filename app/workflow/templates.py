from app.execution_context import ExecutionMode
from app.workflow.template import TaskBlueprint, WorkflowTemplate

def project_delivery_template() -> WorkflowTemplate:
    """从需求澄清到代码、测试和审查的默认交付流程经验。"""
    return WorkflowTemplate(
        id="project_delivery",
        name="项目交付草案",
        description="生成需求、架构、实施任务、首版代码、测试证据和审查报告。",
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
                id="tasks-plan",
                agent_id="task_agent",
                objective="根据需求和架构交付可验收的实施任务决策包。",
                output_key="tasks_plan",
                artifact_key="tasks",
                depends_on=("requirement", "architecture"),
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
                objective="根据架构和任务声明项目 runtime、依赖意图和 sandbox 环境报告。",
                output_key="environment",
                depends_on=("requirement", "architecture", "tasks-quality-gate"),
            ),
            TaskBlueprint(
                id="implementation",
                agent_id="code_agent",
                objective="根据前置产物在 workspace 内实现首版代码并保存实现摘要。",
                output_key="implementation",
                depends_on=("requirement", "architecture", "tasks-quality-gate", "environment"),
            ),
            TaskBlueprint(
                id="tests",
                agent_id="test_agent",
                objective="为 workspace 中的实现编写并运行基础测试，保存测试报告。",
                output_key="tests",
                depends_on=("requirement", "tasks-quality-gate", "environment", "implementation"),
            ),
            TaskBlueprint(
                id="review",
                agent_id="review_agent",
                objective="审查需求、实现和测试证据，保存交付审查报告。",
                output_key="review",
                depends_on=("requirement", "architecture", "tasks-quality-gate", "environment", "implementation", "tests"),
            ),
        ),
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

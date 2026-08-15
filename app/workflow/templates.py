from app.workflow.template import TaskBlueprint, WorkflowTemplate


def requirement_generation_template() -> WorkflowTemplate:
    """当前已实现的需求生成流程经验。"""
    return WorkflowTemplate(
        id="requirement_generation",
        name="需求生成",
        description="将用户目标整理并保存为项目需求文档。",
        nodes=(
            TaskBlueprint(
                id="requirement",
                agent_id="requirement_agent",
                objective="将用户需求整理为结构化需求文档并保存到项目目录。",
                output_key="requirement_result",
            ),
        ),
    )


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
                id="tasks",
                agent_id="task_agent",
                objective="根据需求和架构拆分实施任务并保存任务清单。",
                output_key="tasks",
                depends_on=("requirement", "architecture"),
            ),
            TaskBlueprint(
                id="environment",
                agent_id="bootstrap_agent",
                objective="根据架构和任务声明项目 runtime、依赖意图和 sandbox 环境报告。",
                output_key="environment",
                depends_on=("requirement", "architecture", "tasks"),
            ),
            TaskBlueprint(
                id="implementation",
                agent_id="code_agent",
                objective="根据前置产物在 workspace 内实现首版代码并保存实现摘要。",
                output_key="implementation",
                depends_on=("requirement", "architecture", "tasks", "environment"),
            ),
            TaskBlueprint(
                id="tests",
                agent_id="test_agent",
                objective="为 workspace 中的实现编写并运行基础测试，保存测试报告。",
                output_key="tests",
                depends_on=("requirement", "tasks", "environment", "implementation"),
            ),
            TaskBlueprint(
                id="review",
                agent_id="review_agent",
                objective="审查需求、实现和测试证据，保存交付审查报告。",
                output_key="review",
                depends_on=("requirement", "architecture", "tasks", "environment", "implementation", "tests"),
            ),
        ),
    )

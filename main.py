from app.project.project import Project
from app.tool_manager.gateway import ToolGateway
from app.artifact.store import ArtifactStore
from app.domain import (
    register_architecture_tools,
    register_bootstrap_tools,
    register_code_tools,
    register_requirement_tools,
    register_review_tools,
    register_task_tools,
    register_test_tools,
)
from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.architecture_agent import ArchitectureAgent
from app.agent.bootstrap_agent import BootstrapAgent
from app.agent.code_agent import CodeAgent
from app.agent.requirement_agent import RequirementAgent
from app.agent.task_agent import TaskAgent
from app.agent.test_agent import TestAgent
from app.agent.review_agent import ReviewAgent
from app.workflow.template import WorkflowTemplateRegistry
from app.workflow.templates import project_delivery_template
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.orchestration.trace import TraceStore
from app.planner.planner import CrewAIPlannerRuntime
from app.planner.service import PlannerFailure, PlannerService


def main():
    project_path = "./projects/Mega_Shit"

    # 准备项目
    if not Project.exists("Mega_Shit", "./projects"):
        Project(name="Mega_Shit", base_dir="./projects").create()
        print()

    gateway = ToolGateway()
    traces = TraceStore(project_path)
    register_requirement_tools(gateway, project_path)
    register_architecture_tools(gateway, project_path)
    register_task_tools(gateway, project_path)
    register_bootstrap_tools(gateway, project_path)
    register_code_tools(gateway, project_path)
    register_test_tools(gateway, project_path, traces=traces)
    register_review_tools(gateway, project_path, traces=traces)

    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            id="requirement_agent",
            domain="requirement",
            description="将用户描述整理为结构化需求文档",
            output_key="requirement",
        ),
        factory=lambda: RequirementAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="architecture_agent",
            domain="architecture",
            description="根据需求设计系统边界、模块和接口",
            output_key="architecture",
        ),
        factory=lambda: ArchitectureAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="task_agent",
            domain="task",
            description="根据需求和架构拆分可验收的实施任务",
            output_key="tasks",
        ),
        factory=lambda: TaskAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="bootstrap_agent",
            domain="bootstrap",
            description="声明受支持 runtime、依赖意图和环境验证前置条件",
            output_key="environment",
        ),
        factory=lambda: BootstrapAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="code_agent",
            domain="code",
            description="根据前置产物在受限 workspace 内实现首版代码",
            output_key="implementation",
        ),
        factory=lambda: CodeAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="test_agent",
            domain="test",
            description="为 workspace 实现编写并运行基础单元测试，输出测试证据",
            output_key="tests",
        ),
        factory=lambda: TestAgent(gateway),
    )
    agents.register(
        AgentDefinition(
            id="review_agent",
            domain="review",
            description="审查需求、实现与测试证据，输出交付风险和结论",
            output_key="review",
        ),
        factory=lambda: ReviewAgent(gateway),
    )

    templates = WorkflowTemplateRegistry()
    templates.register(project_delivery_template())
    goal = "我要做一个检测粪便健康的网站，请完成需求、架构、任务、首版代码、基础测试和交付审查。"
    planner = PlannerService(
        runtime=CrewAIPlannerRuntime(),
        agents=agents,
        templates=templates,
        artifacts=ArtifactStore(project_path),
        traces=traces,
    )
    try:
        planning = planner.plan(goal=goal, plan_id="project-delivery-demo")
    except PlannerFailure as error:
        print(f"❌ 规划失败: {error}")
        return

    plan = planning.plan
    print("规划工作项: " + " -> ".join(item.agent_id for item in plan.work_items))
    result = GraphRunner(agents, gateway, traces=traces).run(plan)

    print("=" * 60)
    print(result.state.artifacts.get("review") or result.error or "流程未返回内容")
    print("=" * 60)
    if result.status is GraphRunStatus.COMPLETED:
        print(f"✅ 已生成并审查项目交付，产物位于 {project_path}")
    elif result.status is GraphRunStatus.WAITING_FOR_CAPABILITY_APPROVAL:
        names = ", ".join(source.name for source in result.candidate_sources)
        print(f"等待授权使用外部能力，可选来源: {names}")
    else:
        print(f"流程状态: {result.status.value}")


if __name__ == "__main__":
    main()

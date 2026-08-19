"""Task domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.registry import AgentDefinition
from app.agent.task_agent import TaskAgent
from app.domain.task.tools import register_task_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_task_tools(container.gateway, container.project_path)
    container.agents.register(
        AgentDefinition(
            id="task_agent", domain="task",
            description="在受控产物工作流中拆分可验收的实施任务", output_key="tasks",
            artifact_key="tasks",
        ),
        factory=lambda: TaskAgent(container.gateway),
    )

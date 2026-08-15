"""TaskAgent 的工具合同。"""

from app.domain.task.service import TaskService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class TaskToolSet:
    """将 TaskService 适配为任务分解 Agent 的本地工具。"""

    def __init__(self, service: TaskService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_tasks(self, content: str) -> str:
        return self._service.save_tasks(content)


def register_task_tools(gateway: ToolGateway, project_path: str) -> None:
    """任务节点可读取需求和架构，只能写入 tasks.md。"""
    tools = TaskToolSet(TaskService(project_path))
    gateway.register_toolset(
        domain="task",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物。可读取: requirement、architecture。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "artifact": {"type": "string", "description": "前置产物标识"}
                            },
                            "required": ["artifact"],
                        },
                    ),
                    tools.load_artifact,
                ),
                (
                    ToolDef(
                        name="save_tasks",
                        description="保存完整实施任务清单到 tasks.md。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "完整 Markdown 内容"}
                            },
                            "required": ["content"],
                        },
                    ),
                    tools.save_tasks,
                ),
            ]
        ),
    )

"""TaskAgent 的标准受控工具合同。"""

from app.domain.task.service import TaskArtifactWorkflow
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef


def register_task_tools(gateway: ToolGateway, project_path: str) -> None:
    """只注册引用读取、暂存写入和候选创建三类标准能力。"""
    workflow = TaskArtifactWorkflow(project_path)
    gateway.register_toolset(
        domain="task",
        name="artifact_workflow",
        toolset=ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="load_task_input",
                        description="读取当前任务 WorkItem 已授权的需求、架构或暂存引用。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "ref_id": {"type": "string", "description": "任务上下文列出的输入引用"}
                            },
                            "required": ["ref_id"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned", "integration"),
                    ),
                    workflow.load_input,
                ),
                (
                    ToolDef(
                        name="write_staged_tasks",
                        description="将任务清单写入当前 WorkItem 的专属暂存位置，不会发布。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "完整 Markdown 任务清单"}
                            },
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        execution_modes=("partitioned",),
                        completion_policy="final",
                    ),
                    workflow.write_staged,
                ),
                (
                    ToolDef(
                        name="create_tasks_candidate",
                        description="基于已授权暂存输出创建 tasks 候选版本，不会直接发布。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "整合后的完整 Markdown 任务清单"}
                            },
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                        execution_modes=("integration",),
                        completion_policy="final",
                    ),
                    workflow.create_candidate,
                ),
            ]
        ),
    )

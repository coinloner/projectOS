"""ArchitectureAgent 的工具合同。"""

from app.domain.architecture.service import ArchitectureService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class ArchitectureToolSet:
    """将 ArchitectureService 适配为架构 Agent 的本地工具。"""

    def __init__(self, service: ArchitectureService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_architecture(self, content: str) -> str:
        return self._service.save_architecture(content)


def register_architecture_tools(gateway: ToolGateway, project_path: str) -> None:
    """架构节点只能读取需求，并写入 architecture.md。"""
    tools = ArchitectureToolSet(ArchitectureService(project_path))
    gateway.register_toolset(
        domain="architecture",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置需求产物。可读取: requirement。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "artifact": {
                                    "type": "string",
                                    "description": "产物标识 requirement",
                                }
                            },
                            "required": ["artifact"],
                        },
                    ),
                    tools.load_artifact,
                ),
                (
                    ToolDef(
                        name="save_architecture",
                        description="保存完整架构文档到 architecture.md。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "完整 Markdown 内容"}
                            },
                            "required": ["content"],
                        },
                    ),
                    tools.save_architecture,
                ),
            ]
        ),
    )

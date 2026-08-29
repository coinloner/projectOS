"""RequirementAgent 的工具合同。"""

from app.domain.requirement.service import RequirementService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import MCPToolSource, ToolDef, ToolSetSource


class RequirementToolSet:
    """将 RequirementService 适配为 Agent 可调用的本地工具。"""

    def __init__(self, service: RequirementService) -> None:
        self._service = service

    def save_requirement(self, content: str) -> str:
        return self._service.save_requirement(content)

    def load_requirement(self) -> str:
        return self._service.load_requirement()


def register_requirement_tools(gateway: ToolGateway, project_path: str) -> None:
    """注册需求文档读写及默认关闭的外部研究能力。"""
    tools = RequirementToolSet(RequirementService(project_path))
    gateway.register_toolset(
        domain="requirement",
        name="documents",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="save_requirement",
                        description="保存完整需求文档到 requirement.md。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "需求文档的完整 Markdown 内容",
                                }
                            },
                            "required": ["content"],
                        },
                        completion_policy="final",
                    ),
                    tools.save_requirement,
                ),
                (
                    ToolDef(
                        name="load_requirement",
                        description="读取已有 requirement.md，用于修改前了解当前内容。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.load_requirement,
                ),
            ]
        ),
    )
    gateway.register_source(
        domain="requirement",
        name="mcp",
        source=MCPToolSource(),
        capability="external_research",
    )

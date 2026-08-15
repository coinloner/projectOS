"""ReviewAgent 的工具合同。"""

from app.domain.review.service import ReviewService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class ReviewToolSet:
    """将 ReviewService 适配为审查 Agent 的本地工具。"""

    def __init__(self, service: ReviewService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_review(self, content: str) -> str:
        return self._service.save_review(content)

    def list_workspace_files(self) -> str:
        return self._service.list_workspace_files()

    def read_workspace_file(self, path: str) -> str:
        return self._service.read_workspace_file(path)

    def inspect_runtime(self) -> str:
        return self._service.inspect_runtime()


def register_review_tools(gateway: ToolGateway, project_path: str) -> None:
    """审查节点只读阶段摘要和 workspace 摘要，只能写 review.md。"""
    tools = ReviewToolSet(ReviewService(project_path))
    gateway.register_toolset(
        domain="review",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物摘要。可读取: requirement、architecture、tasks、implementation、tests。",
                        parameters={
                            "type": "object",
                            "properties": {"artifact": {"type": "string", "description": "前置产物标识"}},
                            "required": ["artifact"],
                        },
                    ),
                    tools.load_artifact,
                ),
                (
                    ToolDef(
                        name="save_review",
                        description="保存完整交付审查报告到 review.md。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                        },
                    ),
                    tools.save_review,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="review",
        name="workspace_summary",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="list_workspace_files",
                        description="列出 workspace 中允许访问的项目文件。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.list_workspace_files,
                ),
                (
                    ToolDef(
                        name="read_workspace_file",
                        description="读取 workspace 内单个文本文件的受限摘要。",
                        parameters={
                            "type": "object",
                            "properties": {"path": {"type": "string", "description": "workspace 相对路径"}},
                            "required": ["path"],
                        },
                    ),
                    tools.read_workspace_file,
                ),
                (
                    ToolDef(
                        name="inspect_runtime",
                        description="读取当前 runtime、依赖缓存和 sandbox 执行状态摘要。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.inspect_runtime,
                ),
            ]
        ),
    )

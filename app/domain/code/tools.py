"""CodeAgent 的工具合同。"""

from app.domain.code.service import CodeService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class CodeToolSet:
    """将 CodeService 适配为代码 Agent 的本地工具。"""

    def __init__(self, service: CodeService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_implementation(self, content: str) -> str:
        return self._service.save_implementation(content)

    def list_workspace_files(self) -> str:
        return self._service.list_workspace_files()

    def read_workspace_file(self, path: str) -> str:
        return self._service.read_workspace_file(path)

    def write_workspace_file(self, path: str, content: str) -> str:
        return self._service.write_workspace_file(path, content)

    def inspect_runtime(self) -> str:
        return self._service.inspect_runtime()


def register_code_tools(gateway: ToolGateway, project_path: str) -> None:
    """代码节点可读取设计产物，且仅能在 workspace 内写入允许的文本文件。"""
    tools = CodeToolSet(CodeService(project_path))
    gateway.register_toolset(
        domain="code",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物。可读取: requirement、architecture、tasks、environment。",
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
                        name="save_implementation",
                        description="保存 workspace 实现摘要到 implementation.md。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                        },
                    ),
                    tools.save_implementation,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="code",
        name="workspace",
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
                        description="读取 workspace 内允许访问的文本文件。",
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
                        name="write_workspace_file",
                        description="在 workspace 内写入允许的文本项目文件，不能写 tests/（测试目录归测试节点）。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "workspace 相对路径"},
                                "content": {"type": "string", "description": "完整文件内容"},
                            },
                            "required": ["path", "content"],
                        },
                    ),
                    tools.write_workspace_file,
                ),
                (
                    ToolDef(
                        name="inspect_runtime",
                        description="读取当前项目 runtime、依赖缓存和 sandbox 可执行状态摘要。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.inspect_runtime,
                ),
            ]
        ),
    )

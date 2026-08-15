"""BootstrapAgent 的工具合同。"""

from app.domain.bootstrap.service import BootstrapService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class BootstrapToolSet:
    """将 BootstrapService 适配为 Agent 可调用的受限环境声明工具。"""

    def __init__(self, service: BootstrapService) -> None:
        self._service = service

    def configure_runtime(self, profile: str, dependencies: str = "") -> str:
        return self._service.configure_runtime(profile, dependencies)

    def inspect_runtime(self) -> str:
        return self._service.inspect_runtime()

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_environment(self, content: str) -> str:
        return self._service.save_environment(content)


def register_bootstrap_tools(gateway: ToolGateway, project_path: str) -> None:
    tools = BootstrapToolSet(BootstrapService(project_path))
    gateway.register_toolset(
        domain="bootstrap",
        name="runtime_configuration",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="configure_runtime",
                        description="声明受支持 runtime profile 和可选 requirements.in 内容，不执行依赖安装。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "profile": {"type": "string", "description": "Runtime profile，例如 python-stdlib 或 python-pip"},
                                "dependencies": {"type": "string", "description": "requirements.in 内容；无依赖时留空"},
                            },
                            "required": ["profile"],
                        },
                    ),
                    tools.configure_runtime,
                ),
                (
                    ToolDef(
                        name="inspect_runtime",
                        description="读取当前 runtime 的受控状态摘要。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.inspect_runtime,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="bootstrap",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物。可读取: requirement、architecture、tasks。",
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
                        name="save_environment",
                        description="保存环境配置报告到 environment.md。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                        },
                    ),
                    tools.save_environment,
                ),
            ]
        ),
    )

"""BootstrapAgent 的工具合同。"""

from app.domain.bootstrap.service import BootstrapService
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolDef, ToolSetSource


class BootstrapToolSet:
    """将 BootstrapService 适配为 Agent 可调用的受限环境声明工具。"""

    def __init__(self, service: BootstrapService) -> None:
        self._service = service

    def configure_runtime(
        self,
        profile: str,
        dependencies: str | None = None,
        application: str | None = None,
    ) -> str:
        return self._service.configure_runtime(profile, dependencies or "", application)

    def inspect_runtime(self) -> str:
        return self._service.inspect_runtime()

    def prepare_environment(self) -> str:
        return self._service.prepare_environment()

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
                        description=(
                            "声明受支持 runtime profile、可选 requirements.in 内容和可选白名单应用。"
                            "不执行依赖安装。可用 application: static-web（纯静态前端，"
                            "workspace 根目录有 index.html）、todo-web（workspace/backend + "
                            "workspace/frontend）、python-backend（纯 Python 后端，backend/http_adapter.py）、"
                            "fastapi-postgres（FastAPI + PostgreSQL 结构）或 "
                            "fastapi-postgres-web（后端同时挂载 workspace/frontend）。"
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "profile": {"type": "string", "description": "Runtime profile，例如 python-stdlib 或 python-pip"},
                                "dependencies": {"type": "string", "description": "requirements.in 内容；无依赖时留空"},
                                "application": {
                                    "type": "string",
                                    "description": (
                                        "可选的受信应用标识（static-web / todo-web / python-backend / "
                                        "fastapi-postgres / fastapi-postgres-web）；与架构声明的应用形态匹配时声明，"
                                        "不确定时留空，控制面会按项目形状自动探测。"
                                    ),
                                },
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
                (
                    ToolDef(
                        name="prepare_environment",
                        description="由控制平面后台准备受信 Docker 镜像；第三方依赖需要通过控制平面审批。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.prepare_environment,
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
                        description="读取前置产物。可读取: requirement、architecture、architecture_contract、tasks。",
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
                        completion_policy="final",
                    ),
                    tools.save_environment,
                ),
            ]
        ),
    )

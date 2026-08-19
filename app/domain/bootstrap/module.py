"""Bootstrap domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.bootstrap_agent import BootstrapAgent
from app.agent.registry import AgentDefinition
from app.domain.bootstrap.tools import register_bootstrap_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_bootstrap_tools(container.gateway, container.project_path)
    container.agents.register(
        AgentDefinition(
            id="bootstrap_agent", domain="bootstrap",
            description="声明受支持 runtime、依赖意图和环境验证前置条件",
            output_key="environment",
        ),
        factory=lambda: BootstrapAgent(container.gateway),
    )

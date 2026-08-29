"""Code domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.code_agent import CodeAgent
from app.agent.code_integration_agent import CodeIntegrationAgent
from app.agent.registry import AgentDefinition
from app.domain.code.service import CodeIntegrationService
from app.domain.code.tools import register_code_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_code_tools(container.gateway, container.project_path)
    container.agents.register(
        AgentDefinition(
            id="code_agent", domain="code",
            description="根据前置产物在受限 workspace 内实现首版代码",
            output_key="implementation", max_parallel_instances=4,
        ),
        factory=lambda: CodeAgent(container.gateway),
    )
    container.agents.register(
        AgentDefinition(
            id="code_integration_agent", domain="code_integration",
            description="使用 LLM 审核和确定性策略合并已有隔离分区 ChangeSet",
            output_key="implementation_merge", artifact_key="implementation",
        ),
        factory=lambda: CodeIntegrationAgent(
            container.gateway, CodeIntegrationService(container.project_path)
        ),
    )

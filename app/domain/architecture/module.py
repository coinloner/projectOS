"""Architecture domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.architecture_agent import ArchitectureAgent
from app.agent.architecture_contract_agent import ArchitectureContractAgent
from app.agent.registry import AgentDefinition
from app.domain.architecture.tools import register_architecture_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_architecture_tools(container.gateway, container.project_path)
    container.agents.register(
        AgentDefinition(
            id="architecture_agent", domain="architecture",
            description="根据需求设计系统边界、模块和接口", output_key="architecture",
            max_parallel_instances=4,
        ),
        factory=lambda: ArchitectureAgent(container.gateway),
    )
    container.agents.register(
        AgentDefinition(
            id="architecture_contract_agent",
            domain="architecture_contract",
            description="将已发布架构编译为实现单元、路径边界和验收合同",
            output_key="architecture_contract",
            max_parallel_instances=1,
        ),
        factory=lambda: ArchitectureContractAgent(container.gateway),
    )

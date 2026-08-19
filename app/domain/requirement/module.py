"""Requirement domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.registry import AgentDefinition
from app.agent.requirement_agent import RequirementAgent
from app.domain.requirement.tools import register_requirement_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_requirement_tools(container.gateway, container.project_path)
    container.agents.register(
        AgentDefinition(
            id="requirement_agent", domain="requirement",
            description="将用户描述整理为结构化需求文档", output_key="requirement",
        ),
        factory=lambda: RequirementAgent(container.gateway),
    )

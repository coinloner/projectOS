"""Review domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.registry import AgentDefinition
from app.agent.review_agent import ReviewAgent
from app.domain.review.tools import register_review_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_review_tools(
        container.gateway, container.project_path, traces=container.traces
    )
    container.agents.register(
        AgentDefinition(
            id="review_agent", domain="review",
            description="审查需求、实现与测试证据，输出交付风险和结论",
            output_key="review",
        ),
        factory=lambda: ReviewAgent(container.gateway),
    )

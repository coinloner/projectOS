"""Test domain 的运行时注册模块。"""

from typing import TYPE_CHECKING

from app.agent.registry import AgentDefinition
from app.agent.test_agent import TestAgent
from app.domain.test.tools import register_test_tools

if TYPE_CHECKING:
    from app.bootstrap.runtime import ProjectOSContainer


def install(container: "ProjectOSContainer") -> None:
    register_test_tools(
        container.gateway, container.project_path, traces=container.traces
    )
    container.agents.register(
        AgentDefinition(
            id="test_agent", domain="test",
            description="为 workspace 实现编写并运行基础单元测试，输出测试证据",
            output_key="tests",
        ),
        factory=lambda: TestAgent(container.gateway),
    )

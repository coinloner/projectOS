from app.agent.result import (
    AgentResult,
    AgentStatus,
    CapabilityRequest,
    from_llm_content,
)
from app.agent.registry import AgentDefinition, AgentRegistry, AgentRunner

__all__ = [
    "AgentDefinition",
    "AgentResult",
    "AgentRegistry",
    "AgentRunner",
    "AgentStatus",
    "ArchitectureAgent",
    "BaseAgent",
    "BootstrapAgent",
    "CapabilityRequest",
    "CodeAgent",
    "RequirementAgent",
    "ReviewAgent",
    "TaskAgent",
    "TestAgent",
    "from_llm_content",
]


def __getattr__(name: str):
    """避免导入纯结果协议时连带初始化 LLM 相关依赖。"""
    if name == "BaseAgent":
        from app.agent.base_agent import BaseAgent

        return BaseAgent
    if name == "BootstrapAgent":
        from app.agent.bootstrap_agent import BootstrapAgent

        return BootstrapAgent
    if name == "ArchitectureAgent":
        from app.agent.architecture_agent import ArchitectureAgent

        return ArchitectureAgent
    if name == "CodeAgent":
        from app.agent.code_agent import CodeAgent

        return CodeAgent
    if name == "RequirementAgent":
        from app.agent.requirement_agent import RequirementAgent

        return RequirementAgent
    if name == "ReviewAgent":
        from app.agent.review_agent import ReviewAgent

        return ReviewAgent
    if name == "TaskAgent":
        from app.agent.task_agent import TaskAgent

        return TaskAgent
    if name == "TestAgent":
        from app.agent.test_agent import TestAgent

        return TestAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

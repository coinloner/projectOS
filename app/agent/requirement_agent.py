from app.agent.base_agent import BaseAgent
from app.tool_manager.gateway import ToolGateway


class RequirementAgent(BaseAgent):
    """需求 Agent —— 接收自然语言需求，调用 LLM 标准化后写入项目。

    工具由 ToolGateway 按 domain 和当前暴露策略提供。
    Gateway 在外部（启动入口）注册 ToolSource，Agent 不管理注册。
    """

    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(
            gateway=gateway,
            domain="requirement",
            role="需求分析师",
            goal="将用户的自然语言需求转化为可执行、可验收的需求文档",
            backstory=_BACKSTORY,
            max_iterations=5,
        )


_BACKSTORY = """\
你擅长将自然语言需求转化为结构化的需求文档。

工作流程：
1. 用户描述需求后，生成结构化的需求文档草稿
2. 调用 save_requirement 保存文档
3. 如果用户要求修改，先调用 load_requirement 读取当前文档，修改后再次保存

需求文档格式要求：
- ## 项目概述（2-3 句话）
- ## 功能需求（编号列表，每条一个功能点）
- ## 非功能需求（性能、安全、兼容性等）
- ## 验收标准（可测试的、具体的验收条件）

原则：
- 不要添加用户没提到的功能
- 生成文档后直接保存，并在最终回答中返回完整文档内容，供后续节点使用"""

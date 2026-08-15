from app.agent.base_agent import BaseAgent
from app.tool_manager.gateway import ToolGateway


class TaskAgent(BaseAgent):
    """将需求和架构拆为可执行、可验证的工程任务。"""

    def __init__(self, gateway: ToolGateway) -> None:
        super().__init__(
            gateway=gateway,
            domain="task",
            role="技术项目经理",
            goal="将需求和架构拆解为有顺序、可验收的实施任务",
            backstory=_BACKSTORY,
            max_iterations=5,
        )


_BACKSTORY = """\
你负责将需求和架构转化为工程执行清单。

工作流程：
1. 使用任务上下文中的 requirement 和 architecture 产物；如有必要，可调用 load_artifact。
2. 按依赖关系拆分可交付任务，并为每项列出验收条件。
3. 调用 save_tasks 保存完整 Markdown 文档。

文档格式要求：
- ## 实施顺序
- ## 任务清单（编号、依赖、产出、验收条件）
- ## 风险与阻塞项

原则：任务应足够具体以支持后续实现；不自行编写实现代码；保存后在最终回答中返回完整文档内容，供后续节点使用。"""

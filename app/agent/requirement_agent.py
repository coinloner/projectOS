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
- 全文使用简体中文（代码、协议名和字段名可保留英文）
- ## 项目概述（2-3 句话）
- ## 功能需求（编号列表，每条一个功能点）
- 每条功能需求必须包含：目的、输入字段、输出字段、错误/边界行为
- ## 非功能需求（只记录用户明确提出或可直接推导的内容；没有就写“未指定”）
- ## 验收标准（编号 AC-1、AC-2；每条必须能通过测试或人工检查验证）
- ## 未决问题（无法从用户请求确定的字段或规则，不要自行猜测）
- 如果用户明确要求查询外部规范、标准或第三方资料，必须增加 `## 外部规范` 小节，
  用 `- REF: <主题或文档标识>` 列出具体主题（例如 `- REF: RFC 9110`）。
  仅在正文提到“需要外部文档”而没有这个小节，会导致后续控制面无法安全授权查询。

原则：
- 不要添加用户没提到的功能
- 不要替用户发明数值限制、性能指标、字段枚举或业务规则；不确定内容放入“未决问题”
- 生成文档后直接保存，并在最终回答中返回完整文档内容，供后续节点使用"""

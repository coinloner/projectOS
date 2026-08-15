from crewai import Agent, Task

from app.agent.result import AgentResult, from_llm_content
from app.llm.factory import build_llm
from app.tool_manager.gateway import ToolGateway


class BaseAgent:
    """Agent 基类 —— Workflow 中的一个智能执行节点。

    职责：
        - 接收 task
        - 通过 ToolGateway 获取当前获授权的 CrewAI 工具
        - 委托 CrewAI 执行 LLM tool-calling loop
        - 返回执行结果

    不负责：
        - 工具注册     → ToolGateway
        - 工具访问决策 → ToolAccessPolicy（由 GraphRunner 经 ToolGateway 调用）
        - 工具来源     → ToolSource（ToolSet / MCP）
        - Provider 格式与工具调用协议 → CrewAI
        - 跨节点编排   → Workflow
        - 跨 Workflow  → Planner
        - 会话记忆     → Memory
        - Prompt 模板  → Policy
    """

    def __init__(
        self,
        gateway: ToolGateway,
        domain: str,
        role: str,
        goal: str,
        backstory: str,
        max_iterations: int = 10,
    ) -> None:
        self._gateway = gateway
        self._domain = domain
        self._role = role
        self._goal = goal
        self._backstory = backstory
        self._max_iterations = max_iterations

    # ── 入口 ──────────────────────────────────

    def run(self, task: str) -> AgentResult:
        """执行单个任务节点。

        Agent 不参与暴露策略决策。它只从 Gateway 取得当前可用工具；Gateway
        根据 Catalog 和当前 source 授权决定工具集合。

        MCP 由调用方（GraphRunner 的上层）通过 gateway.activate_source() 激活。
        当前工具不足时，Agent 返回结构化 AgentResult，而不自行连接 MCP。
        """
        if not task or not task.strip():
            raise RuntimeError("❌ task 不能为空")

        crew_agent = Agent(
            role=self._role,
            goal=self._goal,
            backstory=f"{self._backstory}\n\n{_CAPABILITY_REQUEST_PROMPT}",
            llm=build_llm(),
            tools=self._gateway.tools_for(self._domain),
            max_iter=self._max_iterations,
            verbose=False,
            allow_delegation=False,
        )
        crew_task = Task(
            description=task,
            expected_output="完成任务后的最终结果，或严格的 capability_request JSON。",
            agent=crew_agent,
        )

        try:
            output = crew_agent.execute_task(crew_task)
        except Exception as error:
            raise RuntimeError(f"❌ CrewAI Agent 执行失败: {error}") from error
        return from_llm_content(str(output))

_CAPABILITY_REQUEST_PROMPT = """\
只使用当前提供的工具完成任务。若当前工具无法完成任务中的关键部分，且缺少的
是外部能力，请不要编造结果、不要自行假设可以联网，也不要说明如何激活工具。
请只返回以下 JSON，不要使用 Markdown 代码块或附加文字：
{"type": "capability_request", "capability": "能力标识", "reason": "缺少该能力的原因"}
若当前工具足以完成任务，则按正常方式回答。
"""

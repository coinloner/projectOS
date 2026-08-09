from app.llm.llm_client import LLMClient
from app.tool_manager.manager import ToolManager


class BaseAgent:
    """Agent 基类 —— Workflow 中的一个智能执行节点。

    职责：
        - 接收 task
        - 通过 ToolManager 发现可用工具（不参与 tier 决策）
        - LLM 自主决定调用哪些工具
        - 返回执行结果

    不负责：
        - 工具注册     → ToolManager
        - 暴露半径决策 → ToolManager（由 Workflow 调用）
        - 工具来源     → ToolSource（ToolSet / 外部 MCP）
        - Provider 格式 → LLMClient.build_*()
        - 跨节点编排   → Workflow
        - 跨 Workflow  → Planner
        - 会话记忆     → Memory
        - Prompt 模板  → Policy
    """

    def __init__(
        self,
        manager: ToolManager,
        domain: str,
        system_prompt: str,
        max_iterations: int = 10,
    ) -> None:
        self._llm = LLMClient()
        self._manager = manager
        self._domain = domain
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations

    # ── 入口 ──────────────────────────────────

    def run(self, task: str) -> str:
        """执行单个任务节点。

        Agent 不参与 tier 决策 —— 它只是说"给我工具"，
        ToolManager 根据本地 ToolSet 和 external 状态决定给多少。

        external 由调用方（Workflow）通过 manager.activate_external() 激活。
        """
        if not task or not task.strip():
            raise RuntimeError("❌ task 不能为空")

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task},
        ]
        tools = self._manager.list_tools(self._domain)

        for i in range(self._max_iterations):
            response = self._llm.invoke(messages, tools=tools)

            if response.tool_calls is None:
                return response.content or ""

            names = ", ".join(tc["name"] for tc in response.tool_calls)
            print(f"  [Agent loop #{i+1}] LLM 请求调用工具: {names}")

            # 拼 provider 消息 → 执行工具 → 结果喂回
            messages.append(
                self._llm.build_assistant_message(response.tool_calls)
            )
            for tc in response.tool_calls:
                result = self._manager.call(
                    tc["name"], tc["arguments"], self._domain
                )
                messages.append(
                    self._llm.build_tool_result(tc["id"], result)
                )

            print(f"  [Agent loop #{i+1}] 工具执行完成，继续对话...")

        raise RuntimeError(
            f"❌ Agent 超过最大迭代次数 ({self._max_iterations})"
        )

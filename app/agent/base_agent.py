from app.llm.llm_client import LLMClient
from app.tool_registry.registry import ToolRegistry


class BaseAgent:
    """Agent 基类 —— Workflow 中的一个智能执行节点。

    职责：
        - 接收 task
        - 通过 ToolRegistry 发现可用工具
        - LLM 自主决定调用哪些工具
        - 返回执行结果

    不负责：
        - 工具注册     → ToolRegistry（启动时集中注册）
        - Provider 格式 → LLMClient.build_*()
        - 跨节点编排   → Workflow
        - 跨 Workflow  → Planner
        - 会话记忆     → Memory
        - Prompt 模板  → Policy
    """

    def __init__(
        self,
        registry: ToolRegistry,
        system_prompt: str,
        max_iterations: int = 10,
    ) -> None:
        self._llm = LLMClient()
        self._registry = registry
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations

    # ── 入口 ──────────────────────────────────

    def run(self, task: str) -> str:
        """执行单个任务节点。

        Workflow 调用此方法，传入 task，获取结果。
        Agent 在内部自主决定调用哪些 Tool、调几次。
        """
        if not task or not task.strip():
            raise RuntimeError("❌ task 不能为空")

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task},
        ]
        tools = self._registry.list_tools()

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
                result = self._registry.call(tc["name"], tc["arguments"])
                messages.append(
                    self._llm.build_tool_result(tc["id"], result)
                )

            print(f"  [Agent loop #{i+1}] 工具执行完成，继续对话...")

        raise RuntimeError(
            f"❌ Agent 超过最大迭代次数 ({self._max_iterations})"
        )

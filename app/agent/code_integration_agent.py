"""代码分区的确定性 Integration 节点。"""

from app.agent.result import AgentResult
from app.execution_context import ExecutionContext, ExecutionMode
from app.domain.code.service import CodeIntegrationService


class CodeIntegrationAgent:
    """不调用 LLM，只执行 Policy 检查和受控 workspace 合并。"""

    def __init__(self, service: CodeIntegrationService) -> None:
        self._service = service

    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        if context is None or context.execution_mode is not ExecutionMode.INTEGRATION:
            raise RuntimeError("CodeIntegrationAgent 只能运行在 INTEGRATION WorkItem")
        return AgentResult.completed(self._service.integrate(context))

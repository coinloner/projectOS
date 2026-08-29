from crewai import Agent, Task
from crewai.events import crewai_event_bus

from app.agent.result import AgentResult, from_llm_content
from app.llm.factory import build_llm
from app.execution_context import ExecutionContext
from app.tool_manager.gateway import ToolGateway
import json
import re


_SERIALIZED_TOOL_CALL = re.compile(
    r"to=functions\.([A-Za-z_][A-Za-z0-9_]*)\s+code:\s*"
)
_LOCAL_FALLBACK_TOOLS = frozenset(
    {
        "load_artifact",
        "save_implementation",
        "save_tests",
        "list_workspace_files",
        "read_workspace_file",
        "write_workspace_file",
        "write_test_file",
        "write_staged_code_file",
        "load_code_input",
        "inspect_runtime",
    }
)


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

    def run(
        self, task: str, *, context: ExecutionContext | None = None
    ) -> AgentResult:
        """执行单个任务节点。

        Agent 不参与暴露策略决策。它只从 Gateway 取得当前可用工具；Gateway
        根据 Catalog 和当前 source 授权决定工具集合。

        动态来源由运行时 grant 授权，并由 ExecutionContext 限定可见范围。
        当前工具不足时，Agent 返回结构化 AgentResult，而不自行连接 MCP。
        """
        if not task or not task.strip():
            raise RuntimeError("❌ task 不能为空")

        # CrewAI registers a process-wide event bus.  Some provider/runtime
        # failures can shut that bus down while the API process itself stays
        # alive; subsequent retries would otherwise fail before an LLM call is
        # scheduled.  Re-initialize the lazy bus only when it is already in
        # that terminal state so normal runs keep the shared executor.
        event_executor = getattr(crewai_event_bus, "_sync_executor", None)
        executor_shutdown = bool(getattr(event_executor, "_shutdown", False))
        if getattr(crewai_event_bus, "_shutting_down", False) or executor_shutdown:
            crewai_event_bus._initialize()

        crew_agent = Agent(
            role=self._role,
            goal=self._goal,
            backstory=f"{self._backstory}\n\n{_LANGUAGE_PROMPT}\n\n{_CAPABILITY_REQUEST_PROMPT}",
            # Respect the deployment/request-level stream setting.  Forcing SSE
            # here breaks providers whose CrewAI adapter cannot parse streamed
            # responses from some OpenAI-compatible gateways.
            llm=build_llm(selection=getattr(context, "llm_selection", None)),
            tools=self._gateway.tools_for(self._domain, context=context),
            max_iter=self._max_iterations,
            verbose=False,
            allow_delegation=False,
        )
        crew_task = Task(
            description=task,
            expected_output="完成任务后的最终结果，或严格的 capability_request JSON。",
            agent=crew_agent,
        )

        progress = getattr(context, "progress", None)
        if progress is not None:
            progress.bind_agent(crew_agent)
        try:
            if progress is not None and progress.cancel_requested():
                raise RuntimeError("Worker 收到取消请求，停止提交新的 LLM 调用")
            output = crew_agent.execute_task(crew_task)
        except Exception as error:
            if progress is not None:
                progress.llm_failed(type("Event", (), {"error": str(error)})())
                progress.failed(str(error))
            raise RuntimeError(f"❌ CrewAI Agent 执行失败: {error}") from error
        finally:
            if progress is not None:
                progress.unbind_agent()
        if progress is not None:
            # execute_task returning is the authoritative non-stream terminal
            # signal when CrewAI did not emit its completed callback.
            progress.llm_completed_from_agent_return()
            progress.completed()
        output_text = str(output)
        # Some OpenAI-compatible gateways return a textual representation of
        # CrewAI's tool-call envelope instead of dispatching the function call.
        # Execute only the known local tools that are currently exposed by the
        # Gateway; unauthorized or external tools are never inferred from text.
        _execute_serialized_local_tools(output_text, self._gateway, self._domain, context)
        return from_llm_content(output_text)


def _execute_serialized_local_tools(
    output: str,
    gateway: ToolGateway,
    domain: str,
    context: ExecutionContext | None,
) -> tuple[str, ...]:
    """Replay gateway-local tool calls rendered as plain text by a provider.

    This is a compatibility guard for providers that do not preserve native
    tool-call messages. It is deliberately allow-listed and authorization
    checked through ``gateway.tools_for``; malformed JSON and unknown tools are
    ignored so ordinary natural-language output remains unchanged.
    """
    matches = tuple(_SERIALIZED_TOOL_CALL.finditer(output))
    if not matches:
        return ()
    exposed = {
        str(tool.name): tool
        for tool in gateway.tools_for(domain, context=context)
        if str(tool.name) in _LOCAL_FALLBACK_TOOLS
    }
    if not exposed:
        return ()
    decoder = json.JSONDecoder()
    executed: list[str] = []
    for match in matches:
        name = match.group(1)
        tool = exposed.get(name)
        if tool is None:
            continue
        start = match.end()
        while start < len(output) and output[start].isspace():
            start += 1
        if start >= len(output) or output[start] != "{":
            continue
        try:
            arguments, _ = decoder.raw_decode(output[start:])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(arguments, dict):
            continue
        try:
            tool.run(**arguments)
        except Exception:
            # The normal CrewAI loop would expose the tool error to the model;
            # a textual fallback cannot continue the conversation, so leave
            # the control-plane delivery gate to classify the missing write.
            continue
        executed.append(name)
    return tuple(executed)

_CAPABILITY_REQUEST_PROMPT = """\
只使用当前提供的工具完成任务。若当前工具无法完成任务中的关键部分，且缺少的
是外部能力，请不要编造结果、不要自行假设可以联网，也不要说明如何激活工具。
请只返回以下 JSON，不要使用 Markdown 代码块或附加文字：
{"type": "capability_request", "capability": "能力标识", "reason": "缺少该能力的原因"}
若当前工具足以完成任务，则按正常方式回答。
"""

_LANGUAGE_PROMPT = """\
语言要求：所有自然语言任务回答、Markdown 文档、质量报告和未决项必须使用简体中文。
代码标识符、文件路径、HTTP 方法、JSON key 和标准技术名词可以保留英文；不要因为技术名词
使用英文而切换整段说明语言。"""

from crewai import Agent, Task
from crewai.events import crewai_event_bus

from app.agent.result import AgentResult, from_llm_content
from app.llm.factory import build_llm
from app.execution_context import ExecutionContext
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolExecutionError, ToolResult, ToolResultStatus
from app.domain.architecture.service import llm_token_budget_for_design
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
        # Architecture structured-design tools may be rendered as plain text
        # by OpenAI-compatible relays; replay only these known local tools.
        "write_architecture_blueprint",
        "write_module_design",
        "write_implementation_design",
        "integrate_architecture_designs",
        "compile_project_contract_from_designs",
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

        available_tools = self._gateway.tools_for(self._domain, context=context)
        visible_tool_names = {str(tool.name) for tool in available_tools}
        crew_agent = Agent(
            role=self._role,
            goal=self._goal,
            backstory=f"{self._backstory}\n\n{_LANGUAGE_PROMPT}\n\n{_CAPABILITY_REQUEST_PROMPT}",
            # Respect the deployment/request-level stream setting.  Forcing SSE
            # here breaks providers whose CrewAI adapter cannot parse streamed
            # responses from some OpenAI-compatible gateways.
            llm=build_llm(
                selection=getattr(context, "llm_selection", None),
                max_tokens=llm_token_budget_for_design(
                    getattr(context, "slot", None),
                    unit_count=getattr(context, "implementation_unit_count", None) or 3,
                ),
            ),
            tools=available_tools,
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
            if isinstance(error, ToolExecutionError):
                # Preserve typed tool failures across the CrewAI boundary so
                # GraphRunner can select a domain-specific recovery path.
                raise
            architecture_error = _architecture_tool_validation_error(error, context)
            if architecture_error is not None:
                raise architecture_error from error
            raise RuntimeError(f"❌ CrewAI Agent 执行失败: {error}") from error
        finally:
            if progress is not None:
                progress.unbind_agent()
        if progress is not None:
            # execute_task returning is the authoritative non-stream terminal
            # signal when CrewAI did not emit its completed callback.
            progress.llm_completed_from_agent_return()
            progress.completed()
        output_text = str(output or "").strip()
        if not output_text:
            # A provider can close an SSE stream without emitting a terminal
            # message or a tool result. Treat an empty return as transport
            # failure; never allow it to become a completed NodeResult.
            raise RuntimeError("Provider stream ended without terminal signal: empty response")
        # Some OpenAI-compatible gateways return a textual representation of
        # CrewAI's tool-call envelope instead of dispatching the function call.
        # Execute only the known local tools that are currently exposed by the
        # Gateway; unauthorized or external tools are never inferred from text.
        _execute_serialized_local_tools(output_text, self._gateway, self._domain, context)
        # Some OpenAI-compatible relays preserve the model's structured design
        # payload but drop the native function-call envelope.  Layered
        # architecture nodes have a single deterministic writer, so replay a
        # bare, schema-valid object through that already-authorized tool.  This
        # is intentionally narrower than the textual tool-call compatibility
        # path: only architecture PARTITIONED slots are eligible, and ordinary
        # JSON/natural-language output is left untouched.
        _replay_structured_architecture_output(
            output_text,
            available_tools,
            context=context,
        )
        result = from_llm_content(output_text)
        if (
            result.capability_request is not None
            and result.capability_request.capability in visible_tool_names
        ):
            # Controlled partition/integration nodes have a domain-specific
            # recovery path in GraphRunner (architecture/code/test local
            # tools must never become external capability grants).  Return
            # the typed AgentResult there so that path can classify the
            # request and issue the correct bounded retry.  Keep the legacy
            # protocol exception for unscoped/direct Agent callers.
            if context is not None and context.execution_mode.value != "exclusive":
                return result
            request = result.capability_request
            protocol_result = ToolResult(
                tool_name=request.capability,
                status=ToolResultStatus.RETRYABLE,
                message=(
                    f"模型将当前已暴露的本地工具 '{request.capability}' 误报为能力缺失："
                    f"{request.reason}"
                ),
                error_type="tool_protocol",
                retryable=True,
                expected_tool=request.capability,
            )
            raise ToolExecutionError(protocol_result)
        return result


def _replay_structured_architecture_output(
    output: str,
    available_tools: list[object],
    *,
    context: ExecutionContext | None,
) -> str | None:
    """Replay a bare architecture design object through its writer tool.

    Providers occasionally return the JSON arguments they intended to pass to
    a function without emitting a function-call message.  Treating that text
    as a completed node loses the staged artifact.  The replay is a bounded
    transport fallback, not a second planner: the WorkItem determines the
    only expected writer and CrewAI's tool wrapper performs the same argument
    validation and authorization as a native call.
    """
    if context is None or context.execution_mode.value != "partitioned":
        return None
    if context.agent_id != "architecture_agent" or context.slot is None:
        return None
    expected = _expected_architecture_writer(context)
    if expected is None:
        return None

    payload = _strict_json_object(output)
    if payload is None or payload.get("type") == "capability_request":
        return None
    expected_depth = 0 if expected == "write_architecture_blueprint" else 1 if expected == "write_module_design" else 2
    if payload.get("depth") != expected_depth:
        return None

    tool = next(
        (candidate for candidate in available_tools if getattr(candidate, "name", None) == expected),
        None,
    )
    if tool is None:
        # The gateway did not expose the writer for this attempt.  Do not infer
        # or call an unauthorized tool; Runner will classify the missing
        # staged output and schedule its normal bounded retry.
        return None
    try:
        return str(tool.run(design=payload))
    except ToolExecutionError:
        raise
    except Exception as error:
        typed = _architecture_tool_validation_error(error, context)
        if typed is not None:
            raise typed from error
        raise


def _strict_json_object(output: str) -> dict[str, object] | None:
    """Parse one leading JSON object, accepting a known trailing capability envelope."""
    candidate = output.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(candidate)
        return payload if isinstance(payload, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        # A relay may concatenate the intended tool arguments and its textual
        # capability fallback.  Recover only the first complete object when
        # the remainder is a capability_request envelope; arbitrary prose is
        # never treated as structured output.
        decoder = json.JSONDecoder()
        try:
            payload, end = decoder.raw_decode(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        remainder = candidate[end:].strip()
        if not remainder:
            return payload
        try:
            trailing = json.loads(remainder)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(trailing, dict) and trailing.get("type") == "capability_request":
            return payload
        return None


def _expected_architecture_writer(context: ExecutionContext | None) -> str | None:
    """Return the sole writer allowed for a layered architecture slot."""
    if context is None or context.agent_id != "architecture_agent":
        return None
    if context.execution_mode.value != "partitioned" or context.slot is None:
        return None
    if context.slot == "blueprint":
        return "write_architecture_blueprint"
    if context.slot.startswith("module-"):
        return "write_module_design"
    if context.slot.startswith("implementation-"):
        return "write_implementation_design"
    return None


def _architecture_tool_validation_error(
    error: BaseException,
    context: ExecutionContext | None,
) -> ToolExecutionError | None:
    """Convert CrewAI's pre-dispatch argument error into a typed tool failure."""
    expected = _expected_architecture_writer(context)
    if expected is None:
        return None
    message = str(error)
    marker = f"Tool '{expected}' arguments validation failed"
    if marker not in message:
        return None
    result = ToolResult(
        tool_name=expected,
        status=ToolResultStatus.RETRYABLE,
        message=message,
        error_type="tool_validation",
        retryable=True,
        expected_tool=expected,
    )
    return ToolExecutionError(result, cause=error)


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
        except ToolExecutionError:
            # A textual fallback cannot provide a correction turn. Propagate a
            # typed local-tool failure so the runner schedules a bounded retry
            # instead of treating the trailing natural-language text as done.
            raise
        except Exception as error:
            raise ToolExecutionError(
                ToolResult.failure(name, error), cause=error
            ) from error
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

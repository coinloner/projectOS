"""无前端依赖的连续对话会话层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from app.application.runs import RunService, StartedRun
from app.application.conversation_intent import ConversationIntent, classify_intent
from app.application.result_summary import ResultSummarizer
from app.orchestration.trace import TraceStore
from app.planner.service import PlannerFailure
from app.llm.config import LLMSelection


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_MESSAGE = 8_000


class ConversationBusy(RuntimeError):
    """同一会话仍有一轮执行未进入终态。"""


@dataclass(frozen=True)
class ConversationTurn:
    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    trace_id: str | None = None
    created_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Conversation:
    id: str
    project_id: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.id,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ConversationActionResult:
    """一次会话动作；只在执行类意图下包含 started_run。"""

    intent: ConversationIntent
    user_turn: ConversationTurn
    message: ConversationTurn | None = None
    started_run: StartedRun | None = None

    @property
    def trace_id(self) -> str | None:
        return self.started_run.trace_id if self.started_run else (self.message.trace_id if self.message else None)

    def __iter__(self):
        """兼容早期 API 的 ``user_turn, started = send_message(...)``。"""
        yield self.user_turn
        if self.started_run is None:
            raise TypeError("该会话动作没有启动运行")
        yield self.started_run


class ConversationStore:
    """每个项目内追加保存会话元数据和消息，不把会话状态混入 Trace。"""

    def __init__(self, project_path: str) -> None:
        self._root = Path(project_path) / ".projectos" / "conversations"
        self._lock = RLock()

    def create(self, project_id: str) -> Conversation:
        now = _now()
        conversation = Conversation(
            id=f"conv-{uuid4().hex[:12]}",
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        path = self._conversation_path(conversation.id)
        path.mkdir(parents=True, exist_ok=False)
        self._write_json(path / "conversation.json", conversation.as_dict())
        return conversation

    def load(self, conversation_id: str) -> Conversation:
        _validate_id("conversation id", conversation_id)
        path = self._conversation_path(conversation_id) / "conversation.json"
        if not path.is_file():
            raise FileNotFoundError(f"会话不存在: {conversation_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Conversation(
            id=str(payload["conversation_id"]),
            project_id=str(payload["project_id"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )

    def append(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        trace_id: str | None = None,
    ) -> ConversationTurn:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("会话消息 role 无效")
        value = str(content).strip()
        if not value or len(value) > _MAX_MESSAGE:
            raise ValueError("会话消息不能为空且不能超过 8000 字符")
        conversation = self.load(conversation_id)
        with self._lock:
            turns = self.turns(conversation_id)
            turn = ConversationTurn(
                id=f"turn-{uuid4().hex[:12]}",
                conversation_id=conversation_id,
                sequence=len(turns) + 1,
                role=role,
                content=value,
                trace_id=trace_id,
                created_at=_now(),
            )
            path = self._conversation_path(conversation_id) / "messages.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(turn.as_dict(), ensure_ascii=False) + "\n")
            updated = Conversation(
                id=conversation.id,
                project_id=conversation.project_id,
                created_at=conversation.created_at,
                updated_at=turn.created_at,
            )
            self._write_json(
                self._conversation_path(conversation_id) / "conversation.json",
                updated.as_dict(),
            )
            return turn

    def turns(self, conversation_id: str, *, limit: int = 100) -> tuple[ConversationTurn, ...]:
        self.load(conversation_id)
        path = self._conversation_path(conversation_id) / "messages.jsonl"
        if not path.is_file():
            return ()
        turns = tuple(
            ConversationTurn(
                id=str(payload["id"]),
                conversation_id=str(payload["conversation_id"]),
                sequence=int(payload["sequence"]),
                role=str(payload["role"]),
                content=str(payload["content"]),
                trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
                created_at=str(payload["created_at"]),
            )
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for payload in [json.loads(line)]
        )
        return turns[-max(1, min(limit, 200)) :]

    def prompt_history(self, conversation_id: str, *, max_chars: int = 8_000) -> str:
        lines = []
        for turn in self.turns(conversation_id, limit=20):
            lines.append(f"[{turn.role}] {turn.content}")
        value = "\n".join(lines)
        if len(value) <= max_chars:
            return value
        return value[-max_chars:]

    def _conversation_path(self, conversation_id: str) -> Path:
        return self._root / conversation_id

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class ConversationService:
    """把每条用户消息转换为一轮动态计划执行。"""

    def __init__(self, *, run_service: RunService) -> None:
        self._run_service = run_service

    def _latest_trace(self, project_path: str, turns: tuple[ConversationTurn, ...]) -> dict[str, object] | None:
        trace_id = next((turn.trace_id for turn in reversed(turns) if turn.trace_id), None)
        if not trace_id:
            return None
        try:
            return TraceStore(project_path).load_trace(trace_id)
        except (FileNotFoundError, ValueError):
            return None

    def create(self, project_path: str, project_id: str) -> Conversation:
        return ConversationStore(project_path).create(project_id)

    def send_message(
        self,
        *,
        project_path: str,
        conversation_id: str,
        content: str,
        llm_selection: LLMSelection | None = None,
        llm_overrides: dict[str, LLMSelection] | None = None,
    ) -> ConversationActionResult:
        store = ConversationStore(project_path)
        conversation = store.load(conversation_id)
        existing_turns = store.turns(conversation_id)
        user_turn = store.append(conversation_id, role="user", content=content)
        intent = classify_intent(content)
        trace = self._latest_trace(project_path, existing_turns)
        trace_status = str(trace.get("status", "")) if trace else ""
        trace_id = str(trace.get("trace_id")) if trace and trace.get("trace_id") else None

        if intent is ConversationIntent.INSPECT_RESULT:
            if not trace_id:
                assistant = store.append(conversation_id, role="assistant", content="当前会话还没有可查看的执行结果。")
            else:
                assistant = store.append(
                    conversation_id,
                    role="assistant",
                    content=ResultSummarizer(project_path).summarize(trace_id).content,
                    trace_id=trace_id,
                )
            return ConversationActionResult(intent, user_turn, assistant)

        if intent is ConversationIntent.CONTINUE:
            if not trace_id:
                content_value = "当前会话还没有可继续的执行任务。"
            elif trace_status in {"planned", "running"}:
                content_value = f"当前任务仍在执行中，Trace：{trace_id}。"
            else:
                content_value = f"最近一次任务已结束，状态：{trace_status}。如需从断点继续，请发送“恢复任务”。"
            assistant = store.append(conversation_id, role="assistant", content=content_value, trace_id=trace_id)
            return ConversationActionResult(intent, user_turn, assistant)

        if intent is ConversationIntent.AUTHORIZE:
            assistant = store.append(conversation_id, role="assistant", content="授权请求需要通过运行控制接口明确批准，当前消息未启动新任务。", trace_id=trace_id)
            return ConversationActionResult(intent, user_turn, assistant)

        if intent is ConversationIntent.RESUME:
            if not trace_id:
                raise PlannerFailure("当前会话没有可恢复的 Trace")
            try:
                started = self._run_service.resume_run(project_path=project_path, trace_id=trace_id)
            except PlannerFailure:
                raise
            assistant = store.append(conversation_id, role="system", content=f"已从 Trace 恢复执行：{trace_id}", trace_id=trace_id)
            return ConversationActionResult(intent, user_turn, assistant, started)

        if trace_status in {"planned", "running"}:
            raise ConversationBusy("当前会话仍有运行中的任务，请先查询会话状态或恢复该 Trace")

        if intent is ConversationIntent.MODIFY_REQUEST:
            if not trace_id:
                raise PlannerFailure("当前会话没有可修改的计划")
            try:
                started = self._run_service.start_patch_plan(
                    project_path=project_path,
                    trace_id=trace_id,
                    change_request=content,
                )
            except PlannerFailure:
                store.append(
                    conversation_id,
                    role="system",
                    content="Planner 无法在局部修改范围内生成合法补丁。",
                    trace_id=trace_id,
                )
                raise
            system_turn = store.append(
                conversation_id,
                role="system",
                content=f"已应用局部计划修改并恢复执行：{started.plan_id}",
                trace_id=started.trace_id,
            )
            return ConversationActionResult(intent, user_turn, system_turn, started)

        if not existing_turns:
            # 会话的首条消息就是完整目标：直接按全量目标规划，
            # 而不是当作"连续会话中的新请求"压缩成最小可执行计划
            goal = content
        else:
            history = store.prompt_history(conversation_id)
            goal = (
                "这是一个连续会话中的新用户请求。请结合历史上下文，只为本轮请求生成最小可执行计划。\n\n"
                f"会话历史：\n{history}\n\n"
                "当前请求已经包含在会话历史最后一条 user 消息中。"
            )
        plan_id = f"{conversation.id}-{uuid4().hex[:8]}"
        try:
            kwargs = {"project_path": project_path, "goal": goal, "plan_id": plan_id}
            if llm_selection is not None:
                kwargs["llm_selection"] = llm_selection
            if llm_overrides:
                kwargs["llm_overrides"] = llm_overrides
            started = self._run_service.start_dynamic_plan(**kwargs)
        except PlannerFailure:
            store.append(
                conversation_id,
                role="system",
                content="Planner 无法为本轮请求生成合法执行计划。",
            )
            raise
        except Exception as error:
            # Provider/API failures are part of the conversation contract: the
            # caller receives a PlannerFailure and the persisted conversation
            # retains an actionable assistant-visible diagnostic.
            message = f"本轮模型规划未完成：{type(error).__name__}: {error}"
            store.append(conversation_id, role="assistant", content=message)
            raise PlannerFailure(message) from error
        system_turn = store.append(
            conversation_id,
            role="system",
            content=f"已创建执行 Trace: {started.trace_id}",
            trace_id=started.trace_id,
        )
        return ConversationActionResult(intent, user_turn, system_turn, started)

    def turns(self, project_path: str, conversation_id: str) -> tuple[ConversationTurn, ...]:
        self.sync_terminal_turns(project_path, conversation_id)
        return ConversationStore(project_path).turns(conversation_id)

    def sync_terminal_turns(
        self, project_path: str, conversation_id: str
    ) -> tuple[ConversationTurn, ...]:
        """把 Trace 终态投影为 assistant 消息，避免前端直接理解控制面。"""
        store = ConversationStore(project_path)
        turns = store.turns(conversation_id)
        known_assistant_traces = {
            turn.trace_id
            for turn in turns
            if turn.role == "assistant" and turn.trace_id
        }
        trace_ids = {
            turn.trace_id
            for turn in turns
            if turn.role == "system" and turn.trace_id
        }
        for trace_id in trace_ids - known_assistant_traces:
            try:
                trace = TraceStore(project_path).load_trace(trace_id)
            except (FileNotFoundError, ValueError):
                continue
            status = str(trace.get("status", ""))
            if status in {"planned", "running"}:
                continue
            error = trace.get("error")
            try:
                content = ResultSummarizer(project_path).summarize(trace_id).content
            except (FileNotFoundError, ValueError):
                content = f"本轮执行已结束，状态：{status}。" + (f" 原因：{error}" if error else "")
            store.append(
                conversation_id,
                role="assistant",
                content=content,
                trace_id=trace_id,
            )
        return store.turns(conversation_id)


def _validate_id(name: str, value: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} 格式无效")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

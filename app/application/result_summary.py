"""从一次 Trace 的持久化事实生成面向用户的有限长度摘要。"""

from __future__ import annotations

from dataclasses import dataclass

from app.artifact.store import ArtifactStore
from app.orchestration.trace import TraceContext, TraceStore


@dataclass(frozen=True)
class ResultSummary:
    trace_id: str
    status: str
    content: str


class ResultSummarizer:
    """只读取控制面事实，不让摘要模型创造执行结论。"""

    _ARTIFACTS = ("requirement", "architecture", "tasks", "implementation", "tests", "review")

    def __init__(self, project_path: str, *, max_chars: int = 2_000) -> None:
        if max_chars < 200:
            raise ValueError("max_chars 至少为 200")
        self._traces = TraceStore(project_path)
        self._artifacts = ArtifactStore(project_path)
        self._max_chars = max_chars

    def summarize(self, trace_id: str) -> ResultSummary:
        trace = self._traces.load_trace(trace_id)
        status = str(trace.get("status", "unknown"))
        events = self._traces.list_events(trace_id)
        completed = sum(event.get("type") == "work_item_completed" for event in events)
        failed = sum(event.get("type") == "work_item_failed" for event in events)
        waiting = sum(event.get("type") == "work_item_waiting_capability" for event in events)
        evidence = self._evidence_count(trace)
        artifacts = [key for key in self._ARTIFACTS if self._artifacts.exists(key)]

        if status in {"planned", "running"}:
            lines = [f"本轮执行当前状态：{status}。"]
        else:
            lines = [f"本轮执行已结束，状态：{status}。"]
        if trace.get("goal"):
            lines.append(f"目标：{_single_line(str(trace['goal']), 240)}")
        lines.append(f"步骤：完成 {completed}，失败 {failed}，等待授权 {waiting}。")
        if evidence:
            lines.append(f"验证证据：已记录 {evidence} 条 SandboxEvidence。")
        if artifacts:
            lines.append("当前项目已有产物：" + "、".join(artifacts) + "。")
        error = trace.get("error")
        if error:
            lines.append(f"原因：{_single_line(str(error), 500)}")
        if status in {"failed", "blocked", "needs_replan", "waiting_for_capability_approval"}:
            lines.append("建议：查看运行事件和证据后，再选择继续、恢复或修改需求。")
        return ResultSummary(trace_id=trace_id, status=status, content=_limit(" ".join(lines), self._max_chars))

    def _evidence_count(self, trace: dict[str, object]) -> int:
        try:
            context = TraceContext(
                requirement_id=str(trace["requirement_id"]),
                trace_id=str(trace["trace_id"]),
                parent_trace_id=(str(trace["parent_trace_id"]) if trace.get("parent_trace_id") else None),
            )
            return len(self._traces.list_sandbox_evidence(context))
        except (KeyError, ValueError, TypeError):
            return 0


def _single_line(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _limit(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"

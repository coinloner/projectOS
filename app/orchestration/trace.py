"""一次编排运行的可持久化追踪记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.execution_context import ExecutionContext
from app.orchestration.evidence import SandboxEvidence
from app.sandbox.result import SandboxResult, SandboxStatus


@dataclass(frozen=True)
class TraceContext:
    requirement_id: str
    trace_id: str
    parent_trace_id: str | None = None

    @classmethod
    def ephemeral(cls) -> "TraceContext":
        return cls(
            requirement_id=f"req-{uuid4().hex[:12]}",
            trace_id=f"tr-{uuid4().hex[:12]}",
        )


class TraceStore:
    """将 ProjectOS 控制面记录写入项目私有的 .projectos 目录。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path)
        self._root = self._project_path / ".projectos"

    def start_trace(self, goal: str, *, parent_trace_id: str | None = None) -> TraceContext:
        requirement = self._load_requirement_metadata()
        context = TraceContext(
            requirement_id=requirement["requirement_id"],
            trace_id=f"tr-{uuid4().hex[:12]}",
            parent_trace_id=parent_trace_id,
        )
        self._write_json(
            self._trace_path(context) / "trace.json",
            {
                **asdict(context),
                "goal": goal,
                "status": "planned",
                "requirement_revision": requirement["current_revision"],
                "created_at": self._now(),
            },
        )
        requirement_document = self._project_path / "requirement.md"
        if requirement_document.is_file():
            self.snapshot_requirement(
                context, requirement_document.read_text(encoding="utf-8")
            )
        return context

    def record_plan(self, plan: "ExecutionPlan") -> None:
        self._write_json(
            self._trace_path(plan.trace) / "plan.json",
            {
                "plan_id": plan.id,
                "template_id": plan.template_id,
                "goal": plan.goal,
                "work_items": [
                    {
                        "id": item.id,
                        "agent_id": item.agent_id,
                        "objective": item.objective,
                        "output_key": item.output_key,
                        "dependencies": [
                            {
                                "work_item_id": dependency.work_item_id,
                                "source": dependency.source.value,
                                "rule_id": dependency.rule_id,
                            }
                            for dependency in item.dependencies
                        ],
                        "acceptance_criteria": list(item.acceptance_criteria),
                    }
                    for item in plan.work_items
                ],
            },
        )
        for item in plan.work_items:
            self.record_event(plan.trace, item.id, "work_item_planned")

    def record_event(
        self,
        trace: TraceContext,
        work_item_id: str,
        event_type: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self._record_event_for_trace_id(
            trace_id=trace.trace_id,
            work_item_id=work_item_id,
            event_type=event_type,
            details=details,
        )

    def finish_trace(
        self,
        trace: TraceContext,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        path = self._trace_path(trace) / "trace.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = status
        payload["finished_at"] = self._now()
        if error is not None:
            payload["error"] = error
        self._write_json(path, payload)

    def snapshot_requirement(self, trace: TraceContext, content: str) -> int:
        metadata = self._load_requirement_metadata()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        revision = metadata["current_revision"]
        if metadata.get("current_digest") != digest:
            revision += 1
            metadata["current_revision"] = revision
            metadata["current_digest"] = digest
            self._write_json(self._requirement_metadata_path, metadata)
            snapshot = self._root / "requirements" / f"revision-{revision}.md"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(content, encoding="utf-8")
        self.record_event(
            trace,
            "requirement",
            "requirement_snapshot",
            details={"revision": revision, "digest": digest},
        )
        self._update_trace_requirement_revision(trace, revision)
        return revision

    def record_sandbox_evidence(
        self,
        context: ExecutionContext,
        result: SandboxResult,
    ) -> SandboxEvidence:
        """保存 Docker 原始结果，并将证据 ID 写入当前 WorkItem 的 Trace 事件。"""
        evidence = SandboxEvidence.from_sandbox_result(
            evidence_id=f"ev-{uuid4().hex[:12]}",
            context=context,
            result=result,
            created_at=self._now(),
        )
        self._write_json(
            self._evidence_path(context.trace_id, evidence.id), evidence.as_dict()
        )
        self._record_event_for_trace_id(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            event_type="sandbox_evidence_recorded",
            details={
                "evidence_id": evidence.id,
                "status": evidence.status.value,
                "check_id": evidence.check_id,
            },
        )
        return evidence

    def list_sandbox_evidence(
        self, context: ExecutionContext
    ) -> tuple[SandboxEvidence, ...]:
        directory = self._trace_root(context.trace_id) / "evidence"
        if not directory.is_dir():
            return ()
        return tuple(
            self._load_sandbox_evidence(path)
            for path in sorted(directory.glob("ev-*.json"))
        )

    def load_sandbox_evidence(
        self, context: ExecutionContext, evidence_id: str
    ) -> SandboxEvidence:
        """仅允许在当前 Trace 中读取指定的 Docker 执行证据。"""
        if not evidence_id.startswith("ev-") or "/" in evidence_id or "\\" in evidence_id:
            raise ValueError("无效的 sandbox evidence id")
        path = self._evidence_path(context.trace_id, evidence_id)
        if not path.is_file():
            raise FileNotFoundError(f"当前 Trace 中不存在 sandbox evidence: '{evidence_id}'")
        return self._load_sandbox_evidence(path)

    @property
    def _requirement_metadata_path(self) -> Path:
        return self._root / "requirement.json"

    def _load_requirement_metadata(self) -> dict[str, object]:
        if self._requirement_metadata_path.exists():
            return json.loads(self._requirement_metadata_path.read_text(encoding="utf-8"))
        metadata: dict[str, object] = {
            "requirement_id": f"req-{uuid4().hex[:12]}",
            "current_revision": 0,
            "current_digest": None,
        }
        self._write_json(self._requirement_metadata_path, metadata)
        return metadata

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _trace_path(self, trace: TraceContext) -> Path:
        return self._trace_root(trace.trace_id)

    def _trace_root(self, trace_id: str) -> Path:
        return self._root / "runs" / trace_id

    def _evidence_path(self, trace_id: str, evidence_id: str) -> Path:
        return self._trace_root(trace_id) / "evidence" / f"{evidence_id}.json"

    @staticmethod
    def _load_sandbox_evidence(path: Path) -> SandboxEvidence:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = SandboxStatus(payload["status"])
        return SandboxEvidence(**payload)

    def _record_event_for_trace_id(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        event_type: str,
        details: dict[str, object] | None = None,
    ) -> None:
        path = self._trace_root(trace_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event_id": f"evt-{uuid4().hex[:12]}",
            "trace_id": trace_id,
            "work_item_id": work_item_id,
            "type": event_type,
            "created_at": self._now(),
            "details": details or {},
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _update_trace_requirement_revision(
        self, trace: TraceContext, revision: int
    ) -> None:
        path = self._trace_path(trace) / "trace.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["requirement_revision"] = revision
        self._write_json(path, payload)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

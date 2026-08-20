"""一次编排运行的可持久化追踪记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.execution_context import ExecutionContext
from app.orchestration.evidence import SandboxEvidence
from app.orchestration.retry import FailurePackage, FailureSignal
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
        self._lock = RLock()

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
        payload = {
                "plan_id": plan.id,
                "template_id": plan.template_id,
                "goal": plan.goal,
                "work_items": [
                    {
                        "id": item.id,
                        "agent_id": item.agent_id,
                        "objective": item.objective,
                        "output_key": item.output_key,
                        "artifact_key": item.artifact_key,
                        "execution_mode": item.execution_mode.value,
                        "input_refs": [
                            {
                                **asdict(ref),
                                "ref_id": ref.ref_id,
                            }
                            for ref in item.input_refs
                        ],
                        "output_slot": item.output_slot,
                        "publish_target": item.publish_target,
                        "candidate_from_work_item_id": item.candidate_from_work_item_id,
                        "dependencies": [
                            {
                                "work_item_id": dependency.work_item_id,
                                "source": dependency.source.value,
                                "rule_id": dependency.rule_id,
                            }
                            for dependency in item.dependencies
                        ],
                        "acceptance_criteria": list(item.acceptance_criteria),
                        "constraints": list(item.constraints),
                        "non_goals": list(item.non_goals),
                        "policy_id": item.policy_id,
                        "failure_package": (
                            {
                                "signal": item.failure_package.signal.as_dict(),
                                "check_id": item.failure_package.check_id,
                                "runtime_profile": item.failure_package.runtime_profile,
                                "exit_code": item.failure_package.exit_code,
                                "stdout_excerpt": item.failure_package.stdout_excerpt,
                                "stderr_excerpt": item.failure_package.stderr_excerpt,
                            }
                            if item.failure_package is not None
                            else None
                        ),
                    }
                    for item in plan.work_items
                ],
            }
        self._write_json(self._trace_path(plan.trace) / "plan.json", payload)
        self._write_json(
            self._trace_path(plan.trace) / "plans" / f"{plan.id}.json", payload
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

    def load_trace(self, trace_id: str) -> dict[str, object]:
        """读取 API 可展示的 Trace 摘要，不暴露任意文件路径。"""
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "trace.json"
        if not path.is_file():
            raise FileNotFoundError(f"Trace 不存在: {trace_id}")
        return self._read_json(path)

    def list_events(self, trace_id: str) -> tuple[dict[str, object], ...]:
        """按写入顺序返回一次 Trace 的控制面事件。"""
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "events.jsonl"
        if not path.is_file():
            return ()
        return tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def record_checkpoint(
        self, trace: TraceContext, state: dict[str, object]
    ) -> None:
        """持久化最近一次可恢复状态，Memory 仍保留追加式审计副本。"""
        payload = {
            "schema_version": 1,
            "trace_id": trace.trace_id,
            "updated_at": self._now(),
            "state": state,
        }
        self._write_json(self._trace_path(trace) / "checkpoint.json", payload)

    def load_checkpoint(self, trace_id: str) -> dict[str, object]:
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "checkpoint.json"
        if not path.is_file():
            raise FileNotFoundError(f"Trace 没有可恢复 checkpoint: {trace_id}")
        payload = self._read_json(path)
        if payload.get("schema_version") != 1 or payload.get("trace_id") != trace_id:
            raise ValueError("checkpoint 元数据无效")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("checkpoint.state 格式无效")
        return state

    def load_plan(self, trace_id: str) -> "ExecutionPlan":
        """从持久化计划重建对象，并重新触发计划模型校验。"""
        self._validate_trace_id(trace_id)
        payload = self._read_json(self._trace_root(trace_id) / "plan.json")
        trace_payload = self.load_trace(trace_id)
        from app.artifact.repository import ArtifactRef
        from app.execution_context import ExecutionMode
        from app.orchestration.plan import ExecutionPlan
        from app.orchestration.retry import FailurePackage, FailureSignal
        from app.orchestration.work_item import (
            DependencySource,
            WorkItem,
            WorkItemDependency,
        )

        def ref_from_dict(value: dict[str, object]) -> ArtifactRef:
            return ArtifactRef(
                artifact_key=str(value["artifact_key"]),
                layer=str(value["layer"]),
                trace_id=str(value["trace_id"]) if value.get("trace_id") else None,
                work_item_id=(
                    str(value["work_item_id"])
                    if value.get("work_item_id")
                    else None
                ),
                slot=str(value["slot"]) if value.get("slot") else None,
                revision_id=(
                    str(value["revision_id"])
                    if value.get("revision_id")
                    else None
                ),
            )

        items: list[WorkItem] = []
        for raw in payload.get("work_items", []):
            if not isinstance(raw, dict):
                raise ValueError("plan.json 包含无效 WorkItem")
            failure_payload = raw.get("failure_package")
            failure_package = None
            if isinstance(failure_payload, dict):
                signal_payload = failure_payload.get("signal")
                if isinstance(signal_payload, dict):
                    failure_package = FailurePackage(
                        signal=FailureSignal.from_dict(signal_payload),
                        check_id=(
                            str(failure_payload["check_id"])
                            if failure_payload.get("check_id")
                            else None
                        ),
                        runtime_profile=(
                            str(failure_payload["runtime_profile"])
                            if failure_payload.get("runtime_profile")
                            else None
                        ),
                        exit_code=(
                            int(failure_payload["exit_code"])
                            if failure_payload.get("exit_code") is not None
                            else None
                        ),
                        stdout_excerpt=str(failure_payload.get("stdout_excerpt", "")),
                        stderr_excerpt=str(failure_payload.get("stderr_excerpt", "")),
                    )
            dependencies = tuple(
                WorkItemDependency(
                    work_item_id=str(dep["work_item_id"]),
                    source=DependencySource(str(dep["source"])),
                    rule_id=str(dep["rule_id"]) if dep.get("rule_id") else None,
                )
                for dep in raw.get("dependencies", [])
            )
            items.append(
                WorkItem(
                    id=str(raw["id"]),
                    agent_id=str(raw["agent_id"]),
                    objective=str(raw["objective"]),
                    output_key=str(raw["output_key"]),
                    artifact_key=(
                        str(raw["artifact_key"])
                        if raw.get("artifact_key")
                        else None
                    ),
                    failure_package=failure_package,
                    dependencies=dependencies,
                    acceptance_criteria=tuple(
                        str(value) for value in raw.get("acceptance_criteria", [])
                    ),
                    constraints=tuple(
                        str(value) for value in raw.get("constraints", [])
                    ),
                    non_goals=tuple(str(value) for value in raw.get("non_goals", [])),
                    policy_id=(
                        str(raw["policy_id"]) if raw.get("policy_id") else None
                    ),
                    execution_mode=ExecutionMode(
                        str(raw.get("execution_mode", "exclusive"))
                    ),
                    input_refs=tuple(
                        ref_from_dict(value) for value in raw.get("input_refs", [])
                    ),
                    output_slot=(
                        str(raw["output_slot"]) if raw.get("output_slot") else None
                    ),
                    publish_target=(
                        str(raw["publish_target"])
                        if raw.get("publish_target")
                        else None
                    ),
                    candidate_from_work_item_id=(
                        str(raw["candidate_from_work_item_id"])
                        if raw.get("candidate_from_work_item_id")
                        else None
                    ),
                )
            )
        return ExecutionPlan(
            id=str(payload["plan_id"]),
            goal=str(payload["goal"]),
            template_id=(
                str(payload["template_id"]) if payload.get("template_id") else None
            ),
            trace=TraceContext(
                requirement_id=str(trace_payload["requirement_id"]),
                trace_id=str(trace_payload["trace_id"]),
                parent_trace_id=(
                    str(trace_payload["parent_trace_id"])
                    if trace_payload.get("parent_trace_id")
                    else None
                ),
            ),
            work_items=tuple(items),
        )

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

    def latest_sandbox_evidence(
        self, context: ExecutionContext
    ) -> SandboxEvidence | None:
        evidence = [
            item
            for item in self.list_sandbox_evidence(context)
            if item.work_item_id == context.work_item_id
        ]
        return evidence[-1] if evidence else None

    def failure_package(
        self, trace: TraceContext, signal: FailureSignal
    ) -> FailurePackage:
        """从当前 Trace 组装可交给修复节点的受限诊断包。"""
        if signal.evidence_id is None:
            return FailurePackage(signal=signal)
        path = self._evidence_path(trace.trace_id, signal.evidence_id)
        if not path.is_file():
            return FailurePackage(signal=signal)
        evidence = self._load_sandbox_evidence(path)
        return FailurePackage(
            signal=signal,
            check_id=evidence.check_id,
            runtime_profile=evidence.runtime_profile,
            exit_code=evidence.exit_code,
            stdout_excerpt=_excerpt(evidence.stdout, 1_000),
            stderr_excerpt=_excerpt(evidence.stderr, 2_000),
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

    @staticmethod
    def _validate_trace_id(trace_id: str) -> None:
        if not trace_id or not trace_id.startswith("tr-") or not trace_id[3:].isalnum():
            raise ValueError("trace_id 格式无效")

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
        with self._lock:
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

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))


def _excerpt(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return "[...已截断]\n" + content[-limit:]

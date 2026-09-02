"""一次编排运行的可持久化追踪记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.execution_context import ExecutionContext
from app.orchestration.field_semantics import coalesce_alias
from app.orchestration.evidence import RuntimeEvidence, SandboxEvidence
from app.orchestration.retry import FailurePackage, FailureSignal
from app.sandbox.result import SandboxResult, SandboxStatus
from app.llm.config import LLMSelection
from app.orchestration.delivery import DeliveryStore


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

    @property
    def project_path(self) -> str:
        return str(self._project_path)

    def load_progress(self, trace_id: str) -> dict[str, object] | None:
        """读取 Worker 最近一次真实进度快照。"""
        from app.orchestration.progress import WorkerProgressStore

        return WorkerProgressStore(self.project_path).read(trace_id)

    def metrics(self, trace_id: str) -> dict[str, object]:
        """Return deterministic run metrics from append-only Trace events."""
        events = self.list_events(trace_id)
        counts: dict[str, int] = {}
        for event in events:
            kind = str(event.get("type", "unknown"))
            counts[kind] = counts.get(kind, 0) + 1
        retries = counts.get("work_item_retrying", 0)
        completed = counts.get("work_item_completed", 0)
        failed = counts.get("work_item_failed", 0)
        terminal = completed + failed
        return {
            "trace_id": trace_id,
            "event_count": len(events),
            "work_items_completed": completed,
            "work_items_failed": failed,
            "retry_count": retries,
            "retry_convergence_rate": (completed / terminal) if terminal else None,
            "event_counts": counts,
        }

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
            content = requirement_document.read_text(encoding="utf-8")
            self.snapshot_requirement(context, content)
            DeliveryStore(str(self._project_path)).initialize_from_markdown(content)
        return context

    def record_plan(self, plan: "ExecutionPlan") -> None:
        payload = {
                "plan_id": plan.id,
                "template_id": plan.template_id,
                "process_id": plan.process_id,
                "goal": plan.goal,
                "work_items": [
                    {
                        "id": item.id,
                        "agent_id": item.agent_id,
                        "objective": item.objective,
                        "output_key": item.output_key,
                        "artifact_key": item.artifact_key,
                        "stage_id": item.stage_id,
                        "execution_mode": item.execution_mode.value,
                        "input_refs": [
                            {
                                **asdict(ref),
                                "ref_id": ref.ref_id,
                            }
                            for ref in item.input_refs
                        ],
                        # Canonical persisted name; load_plan accepts legacy
                        # ``output_slot`` from historical traces.
                        "slot": item.slot,
                        "publish_target": item.publish_target,
                        "candidate_from_work_item_id": item.candidate_from_work_item_id,
                        "implementation_unit_id": item.implementation_unit_id,
                        "allowed_paths": list(item.allowed_paths),
                        "forbidden_paths": list(item.forbidden_paths),
                        "required_paths": list(item.required_paths),
                        "wave": item.wave,
                        "owned_files": list(item.owned_files),
                        "delivery_contract": item.delivery_contract,
                        "output_kind": item.output_kind,
                        "contract_digest": item.contract_digest,
                        "policy_refs": list(item.policy_refs),
                        "skill_refs": list(item.skill_refs),
                        "requirement_ids": list(item.requirement_ids),
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
                        "failure_package": (
                            {
                                "signal": item.failure_package.signal.as_dict(),
                                "check_id": item.failure_package.check_id,
                                "runtime_profile": item.failure_package.runtime_profile,
                                "exit_code": item.failure_package.exit_code,
                                "stdout_excerpt": item.failure_package.stdout_excerpt,
                                "stderr_excerpt": item.failure_package.stderr_excerpt,
                                "repair_paths": list(item.failure_package.repair_paths),
                                "forbidden_rework": list(item.failure_package.forbidden_rework),
                                "unsatisfied_constraints": list(item.failure_package.unsatisfied_constraints),
                                "satisfied_constraints": list(item.failure_package.satisfied_constraints),
                                "repair_scope": list(item.failure_package.repair_scope),
                                "owner_files": list(item.failure_package.owner_files),
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
        DeliveryStore(self.project_path).bind_plan(plan)
        for item in plan.work_items:
            self.record_event(plan.trace, item.id, "work_item_planned")

    def record_plan_expansion(self, expansion: dict[str, object]) -> None:
        """Persist dynamic-plan provenance independently from the active plan.

        A dynamic delivery can expand the same plan more than once (Blueprint
        -> modules -> implementations).  Keep the historical ``<plan_id>.json``
        pointer as the latest expansion for existing readers, while also
        writing a kind-specific immutable record so the earlier expansion is
        not overwritten by the next phase.
        """
        trace_id = str(expansion.get("trace_id", ""))
        plan_id = str(expansion.get("plan_id", ""))
        self._validate_trace_id(trace_id)
        if not plan_id or "/" in plan_id or "\\" in plan_id:
            raise ValueError("动态计划扩展缺少合法 plan_id")
        payload = dict(expansion)
        payload.setdefault("schema_version", 1)
        payload["trace_id"] = trace_id
        payload["plan_id"] = plan_id
        expansions_dir = self._trace_root(trace_id) / "expansions"
        self._write_json(expansions_dir / f"{plan_id}.json", payload)
        kind = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(payload.get("kind", "expansion"))).strip("-_.")
        if kind:
            self._write_json(expansions_dir / f"{plan_id}.{kind}.json", payload)

    def load_plan_expansion(self, trace_id: str, plan_id: str) -> dict[str, object]:
        """Read one dynamic expansion record for audit and recovery tooling."""
        self._validate_trace_id(trace_id)
        if not plan_id or "/" in plan_id or "\\" in plan_id:
            raise ValueError("plan_id 格式无效")
        path = self._trace_root(trace_id) / "expansions" / f"{plan_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"计划扩展记录不存在: {plan_id}")
        return self._read_json(path)

    def set_llm_selection(self, trace_id: str, selection: LLMSelection) -> None:
        """将本轮模型选择写入 Trace，供隔离 Worker 和恢复流程使用。"""
        payload = self.load_trace(trace_id)
        payload["llm_selection"] = selection.as_dict()
        self._write_json(self._trace_root(trace_id) / "trace.json", payload)

    def set_llm_overrides(
        self, trace_id: str, overrides: dict[str, LLMSelection]
    ) -> None:
        payload = self.load_trace(trace_id)
        payload["llm_overrides"] = {
            agent_id: selection.as_dict()
            for agent_id, selection in sorted(overrides.items())
        }
        self._write_json(self._trace_root(trace_id) / "trace.json", payload)

    def load_llm_selection(self, trace_id: str) -> LLMSelection | None:
        payload = self.load_trace(trace_id)
        raw = payload.get("llm_selection")
        if not isinstance(raw, dict):
            return None
        required = ("provider", "model", "base_url", "api_key_env", "crewai_provider")
        if any(not str(raw.get(key, "")).strip() for key in required):
            return None
        return LLMSelection(**{key: str(raw[key]) for key in required})

    def load_llm_overrides(self, trace_id: str) -> dict[str, LLMSelection]:
        payload = self.load_trace(trace_id)
        raw_overrides = payload.get("llm_overrides")
        if not isinstance(raw_overrides, dict):
            return {}
        result: dict[str, LLMSelection] = {}
        for agent_id in sorted(raw_overrides):
            raw = raw_overrides[agent_id]
            if not isinstance(raw, dict):
                continue
            required = ("provider", "model", "base_url", "api_key_env", "crewai_provider")
            if any(not str(raw.get(key, "")).strip() for key in required):
                continue
            result[str(agent_id)] = LLMSelection(
                **{key: str(raw[key]) for key in required}
            )
        return result

    def record_plan_baseline(self, plan: "ExecutionPlan", *, revision: int | None = None) -> dict[str, object]:
        """保存可用于局部修改的计划基线，不改变 ExecutionPlan 执行格式。"""
        from app.planner.patch import PlanBaseline

        current_path = self._trace_root(plan.trace.trace_id) / "baseline.json"
        if revision is None and current_path.is_file():
            current = self._read_json(current_path)
            revision = int(current.get("revision", 0)) + 1
        baseline = PlanBaseline.from_plan(plan, revision=revision or 1)
        payload = baseline.as_dict()
        self._write_json(current_path, payload)
        self._write_json(
            self._trace_root(plan.trace.trace_id)
            / "baselines"
            / f"revision-{baseline.revision}.json",
            payload,
        )
        return payload

    def load_plan_baseline(self, trace_id: str) -> dict[str, object]:
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "baseline.json"
        if not path.is_file():
            raise FileNotFoundError(f"Trace 没有计划基线: {trace_id}")
        return self._read_json(path)

    def validate_plan_baseline(self, plan: "ExecutionPlan") -> None:
        """Reject resume when persisted WorkItem contracts differ from baseline."""
        try:
            baseline = self.load_plan_baseline(plan.trace.trace_id)
        except FileNotFoundError:
            return
        raw = baseline.get("contract_digests")
        if not isinstance(raw, dict):
            # Historical baselines did not persist contract digests.
            return
        expected = {str(key): str(value) for key, value in raw.items()}
        actual = {item.id: item.contract_digest or "" for item in plan.work_items}
        if expected != actual:
            missing = sorted(set(expected) - set(actual))
            added = sorted(set(actual) - set(expected))
            changed = sorted(
                item_id
                for item_id in set(expected) & set(actual)
                if expected[item_id] != actual[item_id]
            )
            raise ValueError(
                "ExecutionPlan 与持久化合同基线不一致"
                f"; missing={missing}; added={added}; changed={changed}"
            )

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
        else:
            payload.pop("error", None)
        self._write_json(path, payload)

    def mark_running(self, trace_id: str) -> None:
        """Move a resumable Trace back to an active state before submission."""
        payload = self.load_trace(trace_id)
        payload["status"] = "running"
        payload["started_at"] = self._now()
        payload.pop("finished_at", None)
        payload.pop("error", None)
        self._write_json(self._trace_root(trace_id) / "trace.json", payload)

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

    def load_plan(self, trace_id: str, *, plan_id: str | None = None) -> "ExecutionPlan":
        """从持久化计划重建对象，并重新触发计划模型校验。

        ``plan.json`` 是当前活动计划；带 ``plan_id`` 时从 plans 历史目录读取
        指定版本，供 repair Worker 恢复原始交付 DAG。
        """
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "plan.json"
        if plan_id is not None:
            if not plan_id or "/" in plan_id or "\\" in plan_id:
                raise ValueError("plan_id 格式无效")
            path = self._trace_root(trace_id) / "plans" / f"{plan_id}.json"
        payload = self._read_json(path)
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
                        repair_paths=tuple(str(value) for value in failure_payload.get("repair_paths", [])),
                        forbidden_rework=tuple(str(value) for value in failure_payload.get("forbidden_rework", [])),
                        unsatisfied_constraints=tuple(str(value) for value in failure_payload.get("unsatisfied_constraints", [])),
                        satisfied_constraints=tuple(str(value) for value in failure_payload.get("satisfied_constraints", [])),
                        repair_scope=tuple(str(value) for value in failure_payload.get("repair_scope", [])),
                        owner_files=tuple(str(value) for value in failure_payload.get("owner_files", [])),
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
                    stage_id=(str(raw["stage_id"]) if raw.get("stage_id") else None),
                    failure_package=failure_package,
                    dependencies=dependencies,
                    acceptance_criteria=tuple(
                        str(value) for value in raw.get("acceptance_criteria", [])
                    ),
                    constraints=tuple(
                        str(value) for value in raw.get("constraints", [])
                    ),
                    non_goals=tuple(str(value) for value in raw.get("non_goals", [])),
                    policy_refs=tuple(
                        str(value)
                        for value in raw.get(
                            "policy_refs",
                            ([raw["policy_id"]] if raw.get("policy_id") else []),
                        )
                    ),
                    execution_mode=ExecutionMode(
                        str(raw.get("execution_mode", "exclusive"))
                    ),
                    input_refs=tuple(
                        ref_from_dict(value) for value in raw.get("input_refs", [])
                    ),
                    slot=(
                        str(coalesce_alias(raw, "slot", "output_slot"))
                        if coalesce_alias(raw, "slot", "output_slot")
                        else None
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
                    implementation_unit_id=(
                        str(raw["implementation_unit_id"])
                        if raw.get("implementation_unit_id")
                        else None
                    ),
                    allowed_paths=tuple(str(value) for value in raw.get("allowed_paths", [])),
                    forbidden_paths=tuple(str(value) for value in raw.get("forbidden_paths", [])),
                    required_paths=tuple(str(value) for value in raw.get("required_paths", [])),
                    wave=int(raw.get("wave", 0)),
                    owned_files=tuple(str(value) for value in raw.get("owned_files", [])),
                    delivery_contract=(
                        dict(raw["delivery_contract"])
                        if isinstance(raw.get("delivery_contract"), dict)
                        else None
                    ),
                    output_kind=(
                        str(raw["output_kind"])
                        if raw.get("output_kind")
                        else None
                    ),
                    contract_digest=(
                        str(raw["contract_digest"])
                        if raw.get("contract_digest")
                        else None
                    ),
                    skill_refs=tuple(str(value) for value in raw.get("skill_refs", [])),
                    requirement_ids=tuple(str(value) for value in raw.get("requirement_ids", [])),
                )
            )
        return ExecutionPlan(
            id=str(payload["plan_id"]),
            goal=str(payload["goal"]),
            template_id=(
                str(payload["template_id"]) if payload.get("template_id") else None
            ),
            process_id=str(payload.get("process_id") or "software_delivery"),
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

    def record_delivery_plan(self, plan: "ExecutionPlan", *, overwrite: bool = False) -> None:
        """保存原始交付 DAG，避免局部 repair 计划覆盖恢复基线。"""
        path = self._trace_path(plan.trace) / "delivery-plan.json"
        if path.is_file() and not overwrite:
            return
        self._write_json(path, self._read_json(self._trace_path(plan.trace) / "plans" / f"{plan.id}.json"))

    def load_delivery_plan(self, trace_id: str) -> "ExecutionPlan":
        """读取交付基线计划；兼容历史 Trace 中未写入基线的 repair 记录。"""
        self._validate_trace_id(trace_id)
        root = self._trace_root(trace_id)
        delivery_path = root / "delivery-plan.json"
        if delivery_path.is_file():
            payload = self._read_json(delivery_path)
            plan_id = str(payload.get("plan_id", ""))
            return self.load_plan(trace_id, plan_id=plan_id)

        current = self._read_json(root / "plan.json")
        current_id = str(current.get("plan_id", ""))
        if "-repair-" not in current_id:
            return self.load_plan(trace_id)
        # Older traces predate delivery-plan.json.  Select the first historical
        # plan that is not a repair plan; this is the original project_delivery
        # DAG and is sufficient to migrate them without rewriting history.
        candidates = []
        for path in sorted((root / "plans").glob("*.json")):
            try:
                payload = self._read_json(path)
            except (OSError, ValueError):
                continue
            plan_id = str(payload.get("plan_id", ""))
            if plan_id and "-repair-" not in plan_id:
                candidates.append(plan_id)
        if not candidates:
            return self.load_plan(trace_id)
        return self.load_plan(trace_id, plan_id=candidates[0])

    def record_delivery_checkpoint(self, trace: TraceContext, state: dict[str, object]) -> None:
        """保存 repair 前的原始交付状态，供跨 Worker 恢复。"""
        self._write_json(self._trace_path(trace) / "delivery-checkpoint.json", {
            "schema_version": 1,
            "trace_id": trace.trace_id,
            "updated_at": self._now(),
            "state": state,
        })

    def load_delivery_checkpoint(self, trace_id: str) -> dict[str, object]:
        self._validate_trace_id(trace_id)
        path = self._trace_root(trace_id) / "delivery-checkpoint.json"
        if not path.is_file():
            raise FileNotFoundError(f"Trace 没有交付恢复 checkpoint: {trace_id}")
        payload = self._read_json(path)
        if payload.get("schema_version") != 1 or payload.get("trace_id") != trace_id:
            raise ValueError("交付 checkpoint 元数据无效")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ValueError("交付 checkpoint.state 格式无效")
        return state

    def snapshot_requirement(self, trace: TraceContext, content: str) -> int:
        metadata = self._load_requirement_metadata()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata["external_references"] = _external_references_from_markdown(content)
        revision = metadata["current_revision"]
        if metadata.get("current_digest") != digest:
            revision += 1
            metadata["current_revision"] = revision
            metadata["current_digest"] = digest
            self._write_json(self._requirement_metadata_path, metadata)
            snapshot = self._root / "requirements" / f"revision-{revision}.md"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(content, encoding="utf-8")
        else:
            self._write_json(self._requirement_metadata_path, metadata)
        # A requirement node may write through a direct artifact fallback
        # instead of RequirementService. Keep the delivery matrix initialized
        # from the same canonical snapshot in either path.
        DeliveryStore(str(self._project_path)).initialize_from_markdown(content)
        self.record_event(
            trace,
            "requirement",
            "requirement_snapshot",
            details={"revision": revision, "digest": digest},
        )
        self._update_trace_requirement_revision(trace, revision)
        return revision

    def external_references(self) -> tuple[str, ...]:
        """返回需求对象明确声明、允许使用外部资料核实的主题。"""
        metadata = self._load_requirement_metadata()
        raw = metadata.get("external_references", [])
        return tuple(str(value) for value in raw if isinstance(value, str) and value.strip())

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
        try:
            plan = self.load_plan(context.trace_id)
            item = plan.work_item(context.work_item_id)
            if item is not None:
                DeliveryStore(self.project_path).bind_evidence(
                    item.requirement_ids, evidence_id=evidence.id, runtime=False
                )
        except (FileNotFoundError, ValueError):
            pass
        return evidence

    def record_runtime_evidence(self, evidence: RuntimeEvidence) -> RuntimeEvidence:
        """保存环境/构建/启动阶段证据，并追加可检索事件。"""
        if evidence.trace_id.startswith("tr-") is False:
            raise ValueError("runtime evidence 必须绑定有效 Trace")
        self._write_json(self._evidence_path(evidence.trace_id, evidence.id), evidence.as_dict())
        try:
            plan = self.load_plan(evidence.trace_id)
            item = plan.work_item(evidence.work_item_id) if evidence.work_item_id else None
            if item is not None:
                DeliveryStore(self.project_path).bind_evidence(
                    item.requirement_ids, evidence_id=evidence.id, runtime=True
                )
        except (FileNotFoundError, ValueError):
            pass
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

        # A test WorkItem may execute more than one check (for example the
        # Python unit suite and the Node frontend suite).  The first failed
        # check is not necessarily the complete failure surface.  Merge the
        # latest failed evidence for this WorkItem so repair planning receives
        # all relevant paths and diagnostics in one bounded package.
        related: list[SandboxEvidence] = [evidence]
        evidence_root = self._trace_root(trace.trace_id) / "evidence"
        if evidence_root.is_dir():
            latest_by_check: dict[str, SandboxEvidence] = {evidence.check_id: evidence}
            for candidate_path in evidence_root.glob("ev-*.json"):
                try:
                    candidate = self._load_sandbox_evidence(candidate_path)
                except (OSError, ValueError, TypeError):
                    continue
                if (
                    candidate.trace_id == trace.trace_id
                    and candidate.work_item_id == evidence.work_item_id
                    and candidate.status is not SandboxStatus.PASSED
                ):
                    previous = latest_by_check.get(candidate.check_id)
                    if previous is None or candidate.created_at > previous.created_at:
                        latest_by_check[candidate.check_id] = candidate
            related = list(latest_by_check.values())

        repair_paths = tuple(sorted({
            path
            for item in related
            for path in _infer_repair_paths(item.stdout, item.stderr)
        }))
        # Pytest assertion tracebacks often omit the original import line.
        # Read only the named test files to recover the production module that
        # owns the failing symbol (for example ``app.application.orders``),
        # keeping the repair authorization precise and auditable.
        source_paths = _test_source_import_paths(self._project_path, related)
        test_paths = _infer_test_paths(related)
        repair_paths = tuple(sorted(set(repair_paths) | set(source_paths) | set(test_paths)))
        checks = ",".join(sorted({item.check_id for item in related}))
        stdout = "\n\n".join(
            f"[{item.check_id}]\n{_excerpt(item.stdout, 1_000)}"
            for item in sorted(related, key=lambda value: value.check_id)
            if item.stdout
        )
        stderr = "\n\n".join(
            f"[{item.check_id}]\n{_excerpt(item.stderr, 2_000)}"
            for item in sorted(related, key=lambda value: value.check_id)
            if item.stderr
        )
        return FailurePackage(
            signal=signal,
            check_id=checks or evidence.check_id,
            runtime_profile=evidence.runtime_profile,
            exit_code=evidence.exit_code,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
            repair_paths=repair_paths,
            owner_files=repair_paths,
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
            "external_references": [],
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


_WORKSPACE_PATH = re.compile(
    r"(?:(?:/workspace/)|(?:workspace/))?"
    r"((?:backend|frontend|operations|migrations)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]+)"
)
_PYTHON_MODULE = re.compile(
    r"(?:from|import)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)"
)
_TEST_PATH = re.compile(
    r"(?:(?:/workspace/)|(?:workspace/))?"
    r"tests/(api|application|domain|infrastructure|concurrency|frontend)/"
    r"[A-Za-z0-9_.-]+\.[A-Za-z0-9]+"
)


def _infer_repair_paths(stdout: str, stderr: str) -> tuple[str, ...]:
    """从可信 sandbox 诊断中提取实现责任文件，不把测试文件授予 CodeAgent。"""
    text = "\n".join((stdout, stderr))
    paths: set[str] = set()
    for match in _WORKSPACE_PATH.finditer(text):
        path = match.group(1).replace("\\", "/")
        if not path.startswith("tests/"):
            paths.add(path)
    # ImportError/ModuleNotFoundError 通常只给出 Python module 名称；将其
    # 映射到 backend/app 下的具体文件，供修复计划生成明确 allowed_paths。
    for match in _PYTHON_MODULE.finditer(text):
        module = match.group(1)
        if module.startswith("app."):
            candidate = "backend/" + module.replace(".", "/") + ".py"
            paths.add(candidate)

    # Import errors also include the importer (for example
    # ``backend/app/main.py:6``).  Repairing only the missing module can leave
    # the composition root pointing at a stale symbol or path, so include the
    # concrete traceback file as a bounded alternative.  This keeps the repair
    # window local without authorizing the whole backend tree.
    for match in _WORKSPACE_PATH.finditer(text):
        path = match.group(1).replace("\\", "/")
        if path.endswith(".py") and not path.startswith("tests/"):
            paths.add(path)
    # Pytest often prints only the failing test path for assertion/fixture
    # errors.  When no concrete implementation path was present, derive a
    # conservative layer-scoped fallback from that test path so a repair plan
    # never hands CodeAgent an unconstrained empty allowed_paths set.  This is
    # only a hint: the planner and workspace policy still enforce the final
    # write boundary, and more precise traceback/import paths always win.
    if not paths:
        test_layers = {match.group(1) for match in _TEST_PATH.finditer(text)}
        for layer in sorted(test_layers):
            if layer == "frontend":
                paths.add("frontend/**")
                continue
            paths.add(f"backend/app/{layer}/**")
            if layer == "api":
                # HTTP tests also exercise the FastAPI composition root, which
                # is commonly omitted from the traceback when routes are 404.
                paths.add("backend/app/main.py")
    return tuple(sorted(paths))


def _test_source_import_paths(
    project_path: Path, evidences: list[SandboxEvidence]
) -> tuple[str, ...]:
    """Resolve app.* imports from test files named in sandbox diagnostics."""
    text = "\n".join(
        f"{evidence.stdout}\n{evidence.stderr}" for evidence in evidences
    )
    paths: set[str] = set()
    for match in _TEST_PATH.finditer(text):
        relative = match.group(0).replace("/workspace/", "").lstrip("/")
        source = project_path / "workspace" / relative
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for module_match in _PYTHON_MODULE.finditer(content):
            module = module_match.group(1)
            if module.startswith("app."):
                paths.add("backend/" + module.replace(".", "/") + ".py")
    return tuple(sorted(paths))


def _infer_test_paths(evidences: list[SandboxEvidence]) -> tuple[str, ...]:
    """Return concrete tests/** paths for TestAgent's write boundary."""
    text = "\n".join(
        f"{evidence.stdout}\n{evidence.stderr}" for evidence in evidences
    )
    return tuple(sorted({
        match.group(0).replace("/workspace/", "").lstrip("/")
        for match in _TEST_PATH.finditer(text)
    }))


def _external_references_from_markdown(content: str) -> list[str]:
    """提取显式外部规范声明，避免模型自行把常识升级为文档依赖。

    需求文档可在“外部规范/External References”小节中用 ``- REF: topic``
    或 ``- topic`` 声明。没有该小节时，控制面一律视为无需外部资料。
    """
    active = False
    references: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            active = bool(re.search(r"外部(规范|文档|资料)|external references?", line, re.I))
            continue
        if not active:
            continue
        match = re.match(r"[-*]\s*(?:REF:\s*)?(.+)$", line, re.I)
        if match:
            references.append(match.group(1).strip())
    return list(dict.fromkeys(item for item in references if item))

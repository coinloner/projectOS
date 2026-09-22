"""支持并行产出的受控产物仓库。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.artifact.store import ArtifactStore


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class ArtifactRef:
    """一个可由受信执行上下文引用的只读产物版本。"""

    artifact_key: str
    layer: str
    trace_id: str | None = None
    work_item_id: str | None = None
    slot: str | None = None
    revision_id: str | None = None

    @classmethod
    def published(cls, artifact_key: str, revision_id: str | None = None) -> "ArtifactRef":
        return cls(artifact_key=artifact_key, layer="published", revision_id=revision_id)

    @classmethod
    def staged(
        cls, *, artifact_key: str, trace_id: str, work_item_id: str, slot: str
    ) -> "ArtifactRef":
        return cls(
            artifact_key=artifact_key,
            layer="staged",
            trace_id=trace_id,
            work_item_id=work_item_id,
            slot=slot,
        )

    def __post_init__(self) -> None:
        _validate_id("artifact_key", self.artifact_key)
        if self.layer == "published":
            if any(value is not None for value in (self.trace_id, self.work_item_id, self.slot)):
                raise ValueError("published ArtifactRef 不能包含暂存位置")
            if self.revision_id is not None:
                _validate_id("revision_id", self.revision_id)
            return
        if self.layer != "staged":
            raise ValueError("ArtifactRef.layer 必须是 published 或 staged")
        for field_name in ("trace_id", "work_item_id", "slot"):
            _validate_id(field_name, getattr(self, field_name))

    @property
    def ref_id(self) -> str:
        """稳定的受限标识，可作为工具参数而不是文件路径。"""
        if self.layer == "published":
            return f"published:{self.artifact_key}:{self.revision_id or 'current'}"
        return f"staged:{self.trace_id}:{self.work_item_id}:{self.slot}"


@dataclass(frozen=True)
class StagedArtifact:
    ref: ArtifactRef
    digest: str
    created_at: str


@dataclass(frozen=True)
class ArtifactCommitReceipt:
    """统一的、可审计的 staged 交付完成凭证。

    Agent 文本和 ``write_staged`` 的返回值都不是完成信号；只有经过
    manifest、内容 digest、范围和领域 validator 验证后写入的 receipt 才能
    驱动 WorkItem 完成或恢复。
    """

    artifact_id: str
    artifact_kind: str
    ref: ArtifactRef
    digest: str
    trace_id: str
    work_item_id: str
    slot: str
    validation_status: str = "passed"
    committed_at: str = field(default_factory=lambda: _now())

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "ref": asdict(self.ref),
            "digest": self.digest,
            "trace_id": self.trace_id,
            "work_item_id": self.work_item_id,
            "slot": self.slot,
            "validation_status": self.validation_status,
            "committed_at": self.committed_at,
        }


@dataclass(frozen=True)
class IntermediateArtifact:
    """节点内部可恢复阶段结果；不构成正式交付。"""

    trace_id: str
    work_item_id: str
    phase: str
    input_digest: str
    content_digest: str
    content: str
    created_at: str


@dataclass(frozen=True)
class IntegrationIssue:
    rule_id: str
    summary: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationReport:
    """集成候选的确定性检查结果，质量门只接受无未解决问题的报告。"""

    candidate_id: str
    accepted_output_ids: tuple[str, ...]
    issues: tuple[IntegrationIssue, ...]

    @property
    def status(self) -> str:
        return "ready_for_quality_gate" if not self.issues else "needs_rework"


@dataclass(frozen=True)
class ArtifactCandidate:
    id: str
    artifact_key: str
    trace_id: str
    work_item_id: str
    content: str
    digest: str
    source_refs: tuple[ArtifactRef, ...]
    report: IntegrationReport
    created_at: str
    source_digests: tuple[str, ...] = ()
    contract_digest: str | None = None


class ArtifactRepository:
    """管理暂存输出、集成候选和已发布版本。

    根目录 ``architecture.md`` 仍由 ``ArtifactStore`` 维护，但仅在候选通过质量门
    后更新。模型永远不能传入本类使用的路径，调用方只能提供系统生成的 ID/引用。
    """

    def __init__(self, project_path: str) -> None:
        self._store = ArtifactStore(project_path)
        self._root = Path(project_path) / ".projectos"
        self._lock = RLock()

    def exists(self, artifact_key: str) -> bool:
        """项目根目录下的已知产物文件是否已保存（如 implementation.md）。"""
        self._validate_known_artifact(artifact_key)
        return self._store.exists(artifact_key)

    def load_artifact(self, artifact_key: str) -> str:
        """读取正式产物正文，供控制面质量终态检查使用。"""
        self._validate_known_artifact(artifact_key)
        return self._store.load(artifact_key)

    def save_artifact(self, artifact_key: str, content: str) -> None:
        """保存项目根目录下的已知产物文件（控制面兜底，非候选发布）。"""
        self._validate_known_artifact(artifact_key)
        self._store.save(artifact_key, content)

    def load_versioned_ref(self, ref: ArtifactRef, expected_digest: str | None) -> str:
        content = self.load_ref(ref)
        if expected_digest is not None and _digest(content) != expected_digest:
            raise RuntimeError(f"执行期间输入已改变: {ref.ref_id}")
        return content

    def verify_input_versions(
        self, refs: tuple[ArtifactRef, ...], expected: tuple[str, ...] | None,
    ) -> None:
        if expected is None:
            return
        if len(refs) != len(expected):
            raise RuntimeError("执行输入版本证据不完整")
        for ref, digest in zip(refs, expected):
            if _digest(self.load_ref(ref)) != digest:
                raise RuntimeError(f"执行期间输入已改变: {ref.ref_id}")

    def write_issue_report(
        self, *, trace_id: str, work_item_id: str, requirement_refs: tuple[str, ...],
        parent_artifact_ref: str, conflicting_constraint: str, evidence: str,
        reason: str, suggested_resolution: str,
    ) -> dict[str, object]:
        """Persist a child-to-parent conflict without allowing child mutation.

        This is an observation/replan request, never a formal architecture output.
        The control plane may use it to stop the consumer and replan the parent.
        """
        for name, value in (("trace_id", trace_id), ("work_item_id", work_item_id),
                            ("parent_artifact_ref", parent_artifact_ref)):
            if name != "parent_artifact_ref":
                _validate_id(name, value)
        fields = dict(requirement_refs=list(requirement_refs), parent_artifact_ref=parent_artifact_ref,
                      conflicting_constraint=conflicting_constraint, evidence=evidence,
                      reason=reason, suggested_resolution=suggested_resolution)
        if not all(isinstance(v, str) and v.strip() for k, v in fields.items() if k != "requirement_refs"):
            raise ValueError("issue report fields must be non-empty")
        report = {"schema_version": 1, "trace_id": trace_id, "work_item_id": work_item_id,
                  "status": "needs_replan", **fields, "created_at": _now()}
        path = self._root / "runs" / trace_id / "work-items" / work_item_id / "issue-report.json"
        self._write_json(path, report)
        return report

    def load_issue_report(self, *, trace_id: str, work_item_id: str) -> dict[str, object]:
        for name, value in (("trace_id", trace_id), ("work_item_id", work_item_id)):
            _validate_id(name, value)
        return self._read_json(self._root / "runs" / trace_id / "work-items" / work_item_id / "issue-report.json")

    def write_intermediate(
        self, *, trace_id: str, work_item_id: str, phase: str,
        input_digest: str, content: str,
    ) -> IntermediateArtifact:
        """保存节点内部阶段结果，供同一输入版本的恢复使用。"""
        for name, value in (("trace_id", trace_id), ("work_item_id", work_item_id), ("phase", phase)):
            _validate_id(name, value)
        if not input_digest or not content.strip():
            raise ValueError("中间产物必须包含输入摘要和非空内容")
        result = IntermediateArtifact(trace_id, work_item_id, phase, input_digest, _digest(content), content, _now())
        path = self._root / "runs" / trace_id / "work-items" / work_item_id / "intermediate" / f"{phase}.json"
        self._write_json(path, asdict(result))
        return result

    def load_intermediate(
        self, *, trace_id: str, work_item_id: str, phase: str, expected_input_digest: str,
    ) -> IntermediateArtifact:
        for name, value in (("trace_id", trace_id), ("work_item_id", work_item_id), ("phase", phase)):
            _validate_id(name, value)
        path = self._root / "runs" / trace_id / "work-items" / work_item_id / "intermediate" / f"{phase}.json"
        data = self._read_json(path)
        result = IntermediateArtifact(**data)
        if result.input_digest != expected_input_digest or _digest(result.content) != result.content_digest:
            raise RuntimeError("中间产物输入版本或内容摘要不匹配")
        return result

    def latest_intermediate(
        self, *, trace_id: str, work_item_id: str, phases: tuple[str, ...],
        expected_input_digest: str,
    ) -> IntermediateArtifact | None:
        """Return the newest valid phase in declared order for B recovery.

        Missing/corrupt later phases are not silently accepted; callers can resume
        from the last valid phase while preserving the same input digest.
        """
        for phase in reversed(phases):
            try:
                return self.load_intermediate(
                    trace_id=trace_id, work_item_id=work_item_id, phase=phase,
                    expected_input_digest=expected_input_digest)
            except FileNotFoundError:
                continue
        return None

    def write_staged(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        artifact_key: str,
        slot: str,
        content: str,
        source_refs: tuple[ArtifactRef, ...] | None = None,
        contract_digest: str | None = None,
        expected_source_digests: tuple[str, ...] | None = None,
    ) -> StagedArtifact:
        """向一个预分配 slot 写入 Markdown，不能写入正式产物。"""
        self._validate_known_artifact(artifact_key)
        for name, value in (("trace_id", trace_id), ("work_item_id", work_item_id), ("slot", slot)):
            _validate_id(name, value)
        if not content.strip():
            raise ValueError("暂存产物内容不能为空")

        ref = ArtifactRef.staged(
            artifact_key=artifact_key,
            trace_id=trace_id,
            work_item_id=work_item_id,
            slot=slot,
        )
        created_at = _now()
        digest = _digest(content)
        with self._lock:
            self.verify_input_versions(source_refs or (), expected_source_digests)
            # None means legacy/unknown provenance, not an explicitly empty input set.
            provenance = {} if source_refs is None else {
                "source_refs": [asdict(source) for source in source_refs],
                "source_digests": list(expected_source_digests) if expected_source_digests is not None else [_digest(self.load_ref(source)) for source in source_refs],
                "contract_digest": contract_digest,
            }
            root = self._staged_root(ref)
            path = root / "output" / f"{slot}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self._write_json(
                root / "manifest.json",
                {
                    "artifact_key": artifact_key,
                    "trace_id": trace_id,
                    "work_item_id": work_item_id,
                    "slot": slot,
                    "digest": digest,
                    "created_at": created_at,
                    **provenance,
                },
            )
        return StagedArtifact(ref=ref, digest=digest, created_at=created_at)

    def load_ref(self, ref: ArtifactRef) -> str:
        """读取已声明引用的内容，并验证暂存清单没有被篡改。"""
        self._validate_known_artifact(ref.artifact_key)
        if ref.layer == "published":
            return self._load_published(ref)
        with self._lock:
            manifest_path = self._staged_root(ref) / "manifest.json"
            content_path = self._staged_root(ref) / "output" / f"{ref.slot}.md"
            if not manifest_path.is_file() or not content_path.is_file():
                raise FileNotFoundError(f"暂存产物不存在: {ref.ref_id}")
            manifest = self._read_json(manifest_path)
            content = content_path.read_text(encoding="utf-8")
            if manifest.get("artifact_key") != ref.artifact_key or manifest.get("digest") != _digest(content):
                raise RuntimeError(f"暂存产物清单校验失败: {ref.ref_id}")
            return content

    def verify_staged(
        self,
        ref: ArtifactRef,
        *,
        expected_trace_id: str,
        expected_work_item_id: str,
        expected_slot: str,
        expected_artifact_kind: str,
        validator: Callable[[str], object] | None = None,
        expected_source_refs: tuple[ArtifactRef, ...] | None = None,
        expected_contract_digest: str | None = None,
    ) -> ArtifactCommitReceipt:
        """Validate and commit one staged output through one control-plane gate.

        The method is deliberately the same entry point used by execution and
        recovery. It checks provenance before reading content, then validates
        domain shape, and only then persists a receipt. A receipt is immutable
        evidence that the output—not the model's prose—satisfied the contract.
        """
        if ref.layer != "staged":
            raise ValueError("staged 交付验证只能接受 staged ArtifactRef")
        if ref.trace_id != expected_trace_id or ref.work_item_id != expected_work_item_id:
            raise ValueError(
                f"staged 产物 provenance 不匹配: expected={expected_trace_id}/{expected_work_item_id}, "
                f"actual={ref.trace_id}/{ref.work_item_id}"
            )
        if ref.slot != expected_slot:
            raise ValueError(f"staged 产物 slot 不匹配: expected={expected_slot}, actual={ref.slot}")
        content = self.load_ref(ref)
        if validator is not None:
            validator(content)
        manifest = self._read_json(self._staged_root(ref) / "manifest.json")
        if expected_source_refs is not None:
            if manifest.get("source_refs") != [asdict(source) for source in expected_source_refs]:
                raise RuntimeError(f"staged 输入引用不匹配或缺少版本证据: {ref.ref_id}")
            digests = [_digest(self.load_ref(source)) for source in expected_source_refs]
            if manifest.get("source_digests") != digests:
                raise RuntimeError(f"staged 输入已改变: {ref.ref_id}")
        if (expected_contract_digest is not None
                and manifest.get("contract_digest") != expected_contract_digest):
            raise RuntimeError(f"staged 执行合同已改变或缺失: {ref.ref_id}")
        digest = str(manifest.get("digest") or _digest(content))
        receipt = ArtifactCommitReceipt(
            artifact_id=ref.ref_id,
            artifact_kind=expected_artifact_kind,
            ref=ref,
            digest=digest,
            trace_id=expected_trace_id,
            work_item_id=expected_work_item_id,
            slot=expected_slot,
        )
        with self._lock:
            self._write_json(self._staged_root(ref) / "commit-receipt.json", receipt.as_dict())
        return receipt

    def load_commit_receipt(self, ref: ArtifactRef) -> ArtifactCommitReceipt:
        """Load a receipt and re-verify the referenced staged content."""
        if ref.layer != "staged":
            raise ValueError("commit receipt 只能对应 staged ArtifactRef")
        path = self._staged_root(ref) / "commit-receipt.json"
        if not path.is_file():
            raise FileNotFoundError(f"staged 产物没有 commit receipt: {ref.ref_id}")
        payload = self._read_json(path)
        content = self.load_ref(ref)
        if payload.get("digest") != _digest(content):
            raise RuntimeError(f"commit receipt digest 校验失败: {ref.ref_id}")
        return ArtifactCommitReceipt(
            artifact_id=str(payload["artifact_id"]),
            artifact_kind=str(payload["artifact_kind"]),
            ref=ArtifactRef(**payload["ref"]),
            digest=str(payload["digest"]),
            trace_id=str(payload["trace_id"]),
            work_item_id=str(payload["work_item_id"]),
            slot=str(payload["slot"]),
            validation_status=str(payload.get("validation_status", "passed")),
            committed_at=str(payload["committed_at"]),
        )

    def create_candidate(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        artifact_key: str,
        content: str,
        source_refs: tuple[ArtifactRef, ...],
        contract_digest: str | None = None,
        expected_source_digests: tuple[str, ...] | None = None,
    ) -> ArtifactCandidate:
        """创建待发布候选，并生成不依赖 LLM 判断的基础集成报告。"""
        self._validate_known_artifact(artifact_key)
        _validate_id("trace_id", trace_id)
        _validate_id("work_item_id", work_item_id)
        if not content.strip():
            raise ValueError("候选产物内容不能为空")

        issues: list[IntegrationIssue] = []
        accepted: list[str] = []
        source_digests: list[str] = []
        for ref in source_refs:
            if ref.layer != "staged" or ref.artifact_key != artifact_key:
                issues.append(
                    IntegrationIssue(
                        rule_id="integration.invalid_source",
                        summary="集成候选只能使用同一产物的暂存输出。",
                        evidence_refs=(ref.ref_id,),
                    )
                )
                continue
            try:
                source_content = self.load_ref(ref)
            except (FileNotFoundError, RuntimeError) as error:
                issues.append(
                    IntegrationIssue(
                        rule_id="integration.source_unavailable",
                        summary=str(error),
                        evidence_refs=(ref.ref_id,),
                    )
                )
                continue
            accepted.append(ref.ref_id)
            source_digests.append(_digest(source_content))
        if not source_refs:
            issues.append(
                IntegrationIssue(
                    rule_id="integration.no_staged_outputs",
                    summary="集成候选至少需要一个已声明的暂存输出。",
                )
            )

        candidate_id = f"cand-{uuid4().hex[:12]}"
        report = IntegrationReport(
            candidate_id=candidate_id,
            accepted_output_ids=tuple(accepted),
            issues=tuple(issues),
        )
        candidate = ArtifactCandidate(
            id=candidate_id,
            artifact_key=artifact_key,
            trace_id=trace_id,
            work_item_id=work_item_id,
            content=content,
            digest=_digest(content),
            source_refs=source_refs,
            source_digests=expected_source_digests if expected_source_digests is not None else tuple(source_digests),
            contract_digest=contract_digest,
            report=report,
            created_at=_now(),
        )
        with self._lock:
            self.verify_input_versions(source_refs or (), expected_source_digests)
            root = self._candidate_root(artifact_key, candidate_id)
            root.mkdir(parents=True, exist_ok=False)
            (root / "content.md").write_text(content, encoding="utf-8")
            self._write_json(root / "candidate.json", self._candidate_payload(candidate))
        return candidate

    def candidate_for_work_item(
        self, *, trace_id: str, artifact_key: str, work_item_id: str,
        expected_source_refs: tuple[ArtifactRef, ...] | None = None,
        expected_contract_digest: str | None = None,
    ) -> ArtifactCandidate:
        """返回一个集成工作项创建的唯一候选，歧义时拒绝发布。"""
        self._validate_known_artifact(artifact_key)
        candidates_root = self._artifact_root(artifact_key) / "candidates"
        candidates = [
            self.load_candidate(artifact_key, path.name)
            for path in candidates_root.iterdir()
            if candidates_root.is_dir() and path.is_dir() and (path / "candidate.json").is_file()
        ] if candidates_root.is_dir() else []
        matches = [
            candidate
            for candidate in candidates
            if candidate.trace_id == trace_id and candidate.work_item_id == work_item_id
            and (expected_source_refs is None or candidate.source_refs == expected_source_refs)
            and (expected_contract_digest is None or candidate.contract_digest == expected_contract_digest)
        ]
        # Retain history, but never recover an output built against old inputs.
        current_matches = []
        for candidate in matches:
            try:
                self.verify_candidate_sources(candidate)
            except (FileNotFoundError, RuntimeError, ValueError):
                continue
            current_matches.append(candidate)
        matches = current_matches
        if len(matches) != 1:
            raise RuntimeError(
                f"集成工作项 '{work_item_id}' 必须恰好产生一个候选，当前为 {len(matches)} 个"
            )
        return matches[0]

    def load_candidate(self, artifact_key: str, candidate_id: str) -> ArtifactCandidate:
        self._validate_known_artifact(artifact_key)
        _validate_id("candidate_id", candidate_id)
        root = self._candidate_root(artifact_key, candidate_id)
        payload = self._read_json(root / "candidate.json")
        content = (root / "content.md").read_text(encoding="utf-8")
        if payload["digest"] != _digest(content):
            raise RuntimeError(f"候选内容校验失败: {candidate_id}")
        report_data = payload["report"]
        report = IntegrationReport(
            candidate_id=report_data["candidate_id"],
            accepted_output_ids=tuple(report_data["accepted_output_ids"]),
            issues=tuple(IntegrationIssue(**issue) for issue in report_data["issues"]),
        )
        return ArtifactCandidate(
            id=payload["id"], artifact_key=payload["artifact_key"], trace_id=payload["trace_id"],
            work_item_id=payload["work_item_id"], content=content, digest=payload["digest"],
            source_refs=tuple(ArtifactRef(**ref) for ref in payload["source_refs"]),
            report=report, created_at=payload["created_at"],
            source_digests=tuple(payload.get("source_digests", ())),
            contract_digest=payload.get("contract_digest"),
        )

    def verify_candidate_sources(self, candidate: ArtifactCandidate) -> None:
        """Missing version evidence fails closed, including legacy candidates."""
        if len(candidate.source_digests) != len(candidate.source_refs):
            raise RuntimeError(f"候选缺少完整输入版本证据: {candidate.id}")
        for ref, digest in zip(candidate.source_refs, candidate.source_digests):
            if _digest(self.load_ref(ref)) != digest:
                raise RuntimeError(f"候选输入已改变: {candidate.id}: {ref.ref_id}")

    def promote_candidate(self, candidate_id: str, *, artifact_key: str) -> ArtifactRef:
        """质量门通过后才将候选追加为正式版本并更新根目录兼容投影。"""
        candidate = self.load_candidate(artifact_key, candidate_id)
        if candidate.report.status != "ready_for_quality_gate":
            raise PermissionError(f"候选 '{candidate_id}' 存在未解决的集成问题，不能发布")
        with self._lock:
            self.verify_candidate_sources(candidate)
            current_path = self._artifact_root(artifact_key) / "current.json"
            if current_path.is_file():
                current = self._read_json(current_path)
                if current.get("candidate_id") == candidate.id:
                    # A crash after current.json but before the compatibility
                    # projection/checkpoint must not create a second revision.
                    ref = ArtifactRef.published(artifact_key, current["revision_id"])
                    if (current.get("digest") != candidate.digest
                            or _digest(self.load_ref(ref)) != candidate.digest):
                        raise RuntimeError(f"已发布版本内容校验失败: {candidate.id}")
                    self._store.save(artifact_key, candidate.content)
                    return ref
            revisions = self._artifact_root(artifact_key) / "revisions"
            revisions.mkdir(parents=True, exist_ok=True)
            revision_id = f"rev-{len(list(revisions.glob('rev-*.md'))) + 1:03d}"
            (revisions / f"{revision_id}.md").write_text(candidate.content, encoding="utf-8")
            self._write_json(
                self._artifact_root(artifact_key) / "current.json",
                {
                    "artifact_key": artifact_key,
                    "revision_id": revision_id,
                    "candidate_id": candidate.id,
                    "digest": candidate.digest,
                    "promoted_at": _now(),
                },
            )
            self._store.save(artifact_key, candidate.content)
        return ArtifactRef.published(artifact_key, revision_id)

    def verify_published_candidate(self, candidate: ArtifactCandidate) -> ArtifactRef:
        """Verify publication evidence, not merely a completed gate checkpoint."""
        self.verify_candidate_sources(candidate)
        if candidate.report.status != "ready_for_quality_gate":
            raise RuntimeError(f"候选仍有集成问题: {candidate.id}")
        current = self._read_json(self._artifact_root(candidate.artifact_key) / "current.json")
        if (current.get("candidate_id") != candidate.id
                or current.get("digest") != candidate.digest):
            raise RuntimeError(f"当前发布版本不属于候选: {candidate.id}")
        ref = ArtifactRef.published(candidate.artifact_key, current["revision_id"])
        if (_digest(self.load_ref(ref)) != candidate.digest
                or _digest(self._store.load(candidate.artifact_key)) != candidate.digest):
            raise RuntimeError(f"已发布版本或投影内容校验失败: {candidate.id}")
        return ref

    def current_ref(self, artifact_key: str) -> ArtifactRef | None:
        self._validate_known_artifact(artifact_key)
        path = self._artifact_root(artifact_key) / "current.json"
        if not path.is_file():
            return None
        return ArtifactRef.published(artifact_key, self._read_json(path)["revision_id"])

    def _load_published(self, ref: ArtifactRef) -> str:
        if ref.revision_id is None:
            return self._store.load(ref.artifact_key)
        path = self._artifact_root(ref.artifact_key) / "revisions" / f"{ref.revision_id}.md"
        if not path.is_file():
            raise FileNotFoundError(f"已发布版本不存在: {ref.ref_id}")
        return path.read_text(encoding="utf-8")

    def _candidate_payload(self, candidate: ArtifactCandidate) -> dict[str, object]:
        return {
            "id": candidate.id, "artifact_key": candidate.artifact_key,
            "trace_id": candidate.trace_id, "work_item_id": candidate.work_item_id,
            "digest": candidate.digest, "source_refs": [asdict(ref) for ref in candidate.source_refs],
            "source_digests": list(candidate.source_digests),
            "contract_digest": candidate.contract_digest,
            "report": {
                "candidate_id": candidate.report.candidate_id,
                "accepted_output_ids": list(candidate.report.accepted_output_ids),
                "issues": [asdict(issue) for issue in candidate.report.issues],
            },
            "created_at": candidate.created_at,
        }

    def _staged_root(self, ref: ArtifactRef) -> Path:
        return self._root / "runs" / str(ref.trace_id) / "work-items" / str(ref.work_item_id)

    def _artifact_root(self, artifact_key: str) -> Path:
        return self._root / "artifacts" / artifact_key

    def _candidate_root(self, artifact_key: str, candidate_id: str) -> Path:
        return self._artifact_root(artifact_key) / "candidates" / candidate_id

    def _validate_known_artifact(self, artifact_key: str) -> None:
        ArtifactStore.filename_for(artifact_key)

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_id(name: str, value: str | None) -> None:
    if value is None or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} 必须是受限 ID")


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

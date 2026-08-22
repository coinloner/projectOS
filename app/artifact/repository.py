"""支持并行产出的受控产物仓库。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
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

    def write_staged(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        artifact_key: str,
        slot: str,
        content: str,
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

    def create_candidate(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        artifact_key: str,
        content: str,
        source_refs: tuple[ArtifactRef, ...],
    ) -> ArtifactCandidate:
        """创建待发布候选，并生成不依赖 LLM 判断的基础集成报告。"""
        self._validate_known_artifact(artifact_key)
        _validate_id("trace_id", trace_id)
        _validate_id("work_item_id", work_item_id)
        if not content.strip():
            raise ValueError("候选产物内容不能为空")

        issues: list[IntegrationIssue] = []
        accepted: list[str] = []
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
                self.load_ref(ref)
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
            report=report,
            created_at=_now(),
        )
        with self._lock:
            root = self._candidate_root(artifact_key, candidate_id)
            root.mkdir(parents=True, exist_ok=False)
            (root / "content.md").write_text(content, encoding="utf-8")
            self._write_json(root / "candidate.json", self._candidate_payload(candidate))
        return candidate

    def candidate_for_work_item(
        self, *, trace_id: str, artifact_key: str, work_item_id: str
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
        ]
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
        )

    def promote_candidate(self, candidate_id: str, *, artifact_key: str) -> ArtifactRef:
        """质量门通过后才将候选追加为正式版本并更新根目录兼容投影。"""
        candidate = self.load_candidate(artifact_key, candidate_id)
        if candidate.report.status != "ready_for_quality_gate":
            raise PermissionError(f"候选 '{candidate_id}' 存在未解决的集成问题，不能发布")
        with self._lock:
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

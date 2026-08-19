"""Code domain 的 Git worktree 适配层。

该模块是 CodeAgent 与 GitRepositoryManager 之间的唯一业务适配层。Agent 只
看到 ``write_staged_file`` 和受授权的输入引用，不会看到分支名、Git 命令或
正式 workspace 的写入接口。
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from threading import RLock

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.artifact.store import ArtifactStore
from app.execution_context import ExecutionContext, ExecutionMode
from app.policy.quality import GitCodeIntegrationPolicy, QualityReport
from app.workspace.git_repository import (
    ChangeSet,
    GitRepositoryManager,
    TaskBranch,
)
from app.workspace.store import WorkspaceStore


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class GitCodeStagingService:
    """为代码分区创建 task worktree 并持久化 ChangeSet。"""

    _SCOPE_PREFIXES = {"backend": "backend/", "frontend": "frontend/"}

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path).resolve()
        self._artifacts = ArtifactRepository(str(self._project_path))
        self._git = GitRepositoryManager(str(self._project_path))
        self._root = self._project_path / ".projectos"
        self._lock = RLock()

    @property
    def git(self) -> GitRepositoryManager:
        return self._git

    def load_input(self, context: ExecutionContext, ref_id: str) -> str:
        ref = next((item for item in context.input_refs if item.ref_id == ref_id), None)
        if ref is None:
            raise PermissionError("当前代码分区无权读取该输入引用")
        return self._artifacts.load_ref(ref)

    def write_staged_file(
        self, context: ExecutionContext, path: str, content: str
    ) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有代码分区节点可以写入 Git task worktree")
        slot = context.output_slot or ""
        prefix = self._SCOPE_PREFIXES.get(slot)
        if prefix is None:
            raise PermissionError(f"未知代码分区: {slot}")
        normalized = self._validate_path(path)
        if not normalized.startswith(prefix):
            raise PermissionError(f"代码分区 '{slot}' 只能写入 {prefix} 下的文件")
        if not content.strip():
            raise ValueError("代码文件内容不能为空")

        with self._lock:
            baseline = self._ensure_baseline(context.trace_id)
            previous = self._load_change_set(context.trace_id, context.work_item_id)
            branch = self._branch_for(previous) if previous else self._create_task(
                context, baseline
            )
            target = Path(branch.worktree_path) / "workspace" / normalized
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.read_text(encoding="utf-8") == content:
                return f"代码文件未变化，保留 ChangeSet {previous.commit if previous else branch.branch_name}"
            target.write_text(content, encoding="utf-8")
            change = self._git.commit_task(
                branch,
                owned_paths=(f"workspace/{slot}/**",),
                message=f"projectos: {slot} change {context.work_item_id}",
            )
            self._save_change_set(change, slot=slot)
            return f"已提交代码 ChangeSet: {change.commit} ({normalized})"

    def load_change_set(self, trace_id: str, work_item_id: str) -> ChangeSet:
        with self._lock:
            change = self._load_change_set(trace_id, work_item_id)
        if change is None:
            raise FileNotFoundError(
                f"代码 WorkItem '{work_item_id}' 没有可用的 Git ChangeSet"
            )
        return change

    def load_baseline(self, trace_id: str) -> str:
        with self._lock:
            payload = self._read_json(self._baseline_path(trace_id))
        return str(payload["commit"])

    def _ensure_baseline(self, trace_id: str) -> str:
        path = self._baseline_path(trace_id)
        if path.is_file():
            return str(self._read_json(path)["commit"])
        (self._project_path / "workspace").mkdir(parents=True, exist_ok=True)
        commit = self._git.freeze_baseline(("workspace",))
        self._write_json(
            path,
            {"trace_id": trace_id, "commit": commit, "paths": ["workspace"]},
        )
        return commit

    def _create_task(self, context: ExecutionContext, baseline: str) -> TaskBranch:
        return self._git.create_task_worktree(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            base_commit=baseline,
        )

    @staticmethod
    def _branch_for(change: ChangeSet) -> TaskBranch:
        return TaskBranch(
            trace_id=change.trace_id,
            work_item_id=change.work_item_id,
            branch_name=change.branch_name,
            base_commit=change.base_commit,
            worktree_path=change.worktree_path,
        )

    def _save_change_set(self, change: ChangeSet, *, slot: str) -> None:
        self._write_json(
            self._change_set_path(change.trace_id, change.work_item_id),
            {**asdict(change), "slot": slot},
        )

    def _load_change_set(self, trace_id: str, work_item_id: str) -> ChangeSet | None:
        path = self._change_set_path(trace_id, work_item_id)
        if not path.is_file():
            return None
        payload = self._read_json(path)
        return ChangeSet(
            trace_id=str(payload["trace_id"]),
            work_item_id=str(payload["work_item_id"]),
            branch_name=str(payload["branch_name"]),
            base_commit=str(payload["base_commit"]),
            commit=str(payload["commit"]),
            changed_files=tuple(str(item) for item in payload["changed_files"]),
            worktree_path=str(payload["worktree_path"]),
        )

    def _baseline_path(self, trace_id: str) -> Path:
        return self._root / "runs" / self._safe(trace_id) / "git" / "baseline.json"

    def _change_set_path(self, trace_id: str, work_item_id: str) -> Path:
        return (
            self._root
            / "runs"
            / self._safe(trace_id)
            / "git"
            / "tasks"
            / f"{self._safe(work_item_id)}.json"
        )

    @staticmethod
    def _safe(value: str) -> str:
        if not _SAFE_COMPONENT.fullmatch(value):
            raise ValueError(f"标识不是安全的 ProjectOS 组件: {value}")
        return value

    @staticmethod
    def _validate_path(path: str) -> str:
        candidate = Path(path)
        if not path or candidate.is_absolute() or ".." in candidate.parts:
            raise PermissionError("代码文件路径必须是 workspace 内的相对路径")
        if candidate.suffix.lower() not in WorkspaceStore._ALLOWED_SUFFIXES:
            raise PermissionError("不允许写入该代码文件类型")
        return candidate.as_posix()

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


class GitCodeIntegrationService:
    """按授权 ChangeSet 三方合并并发布正式代码。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path).resolve()
        self._staging = GitCodeStagingService(str(self._project_path))
        self._artifacts = ArtifactStore(str(self._project_path))
        self._policy = GitCodeIntegrationPolicy()
        self._lock = RLock()

    def integrate(self, context: ExecutionContext) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("代码合并只能由 INTEGRATION 节点执行")
        with self._lock:
            outputs_list: list[tuple[ArtifactRef, ChangeSet]] = []
            missing_refs: list[ArtifactRef] = []
            for ref in context.input_refs:
                try:
                    outputs_list.append(
                        (
                            ref,
                            self._staging.load_change_set(
                                context.trace_id, ref.work_item_id or ""
                            ),
                        )
                    )
                except FileNotFoundError:
                    missing_refs.append(ref)
            outputs = tuple(outputs_list)
            report = self._policy.evaluate(outputs, missing_refs=tuple(missing_refs))
            if not report.passed:
                raise RuntimeError(self._format_policy_error(report))

            baseline = self._staging.load_baseline(context.trace_id)
            if any(change.base_commit != baseline for _, change in outputs):
                raise RuntimeError("代码 ChangeSet 与当前 Trace baseline 不一致")
            integration = self._staging.git.create_integration_worktree(
                trace_id=context.trace_id, base_commit=baseline
            )
            merged_commits: list[str] = []
            for _, change in sorted(outputs, key=lambda item: item[1].work_item_id):
                result = self._staging.git.merge_task(integration, change)
                if not result.merged:
                    conflicts = ", ".join(item.path for item in result.conflicts)
                    raise RuntimeError(f"Git 三方合并存在冲突: {conflicts}")
                if result.commit:
                    merged_commits.append(result.commit)

            merge_commit = merged_commits[-1] if merged_commits else baseline
            files = self._staging.git.promote_worktree_changes(
                integration,
                base_commit=baseline,
                commit=merge_commit,
                allowed_prefix="workspace",
            )
            summary = self._summary(
                report=report,
                baseline=baseline,
                changes=tuple(change for _, change in outputs),
                merge_commit=merge_commit,
                files=files,
            )
            self._artifacts.save("implementation", summary)
            return f"Git Policy 通过并发布 {len(files)} 个文件，merge commit: {merge_commit}。"

    @staticmethod
    def _format_policy_error(report: QualityReport) -> str:
        details = "；".join(
            f"{issue.rule_id}: {issue.summary}" for issue in report.issues
        )
        return f"Policy '{report.policy_id}' 拒绝代码合并: {details}"

    @staticmethod
    def _summary(
        *,
        report: QualityReport,
        baseline: str,
        changes: tuple[ChangeSet, ...],
        merge_commit: str,
        files: tuple[str, ...],
    ) -> str:
        lines = [
            "# 实现摘要",
            "",
            "## Git 交付记录",
            f"- baseline: `{baseline}`",
            f"- merge_commit: `{merge_commit}`",
            f"- policy: `{report.policy_id}`",
            "- task_commits:",
        ]
        lines.extend(
            f"  - {change.work_item_id}: `{change.commit}` ({', '.join(change.changed_files)})"
            for change in changes
        )
        lines.extend(["", "## 已发布文件"])
        lines.extend(f"- {path}" for path in files)
        return "\n".join(lines) + "\n"

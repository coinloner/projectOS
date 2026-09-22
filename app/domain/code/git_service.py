"""Code domain 的 Git worktree 适配层。

该模块是 CodeAgent 与 GitRepositoryManager 之间的唯一业务适配层。Agent 只
看到 ``write_staged_file`` 和受授权的输入引用，不会看到分支名、Git 命令或
正式 workspace 的写入接口。
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import fnmatch
import ast
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

    # backend/frontend 保留旧版物理目录约定；root 和其他逻辑分区使用
    # 独立 task worktree，但允许写入合同声明的任意 workspace 相对路径。
    _SCOPE_PREFIXES = {"backend": "backend/", "frontend": "frontend/", "root": ""}
    # Project runtime delivery also needs configuration and executable
    # launch files.  These are safe text artifacts in an isolated Git
    # worktree, while the general workspace editing tool intentionally keeps
    # its narrower application-file allowlist.
    _STAGED_SUFFIXES = WorkspaceStore._ALLOWED_SUFFIXES | {
        ".ini", ".sh", ".ps1", ".command", ".cmd", ".bat", ".sql", ".graphql", ".proto", ".cfg", ".conf"
    }
    _STAGED_FILENAMES = WorkspaceStore._ALLOWED_FILENAMES | {
        ".env.example", "requirements.in", "requirements.txt", "LICENSE"
    }

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
        if ref.layer == "staged":
            change = self.load_change_set(context.trace_id, ref.work_item_id or "")
            root = Path(change.worktree_path)
            sections = [
                f"# 前置实现 ChangeSet {change.work_item_id}",
                f"- commit: {change.commit}",
                f"- files: {', '.join(change.changed_files)}",
                "",
            ]
            for changed in change.changed_files:
                relative = changed.removeprefix("workspace/")
                source = root / changed
                if not source.is_file():
                    continue
                try:
                    content = source.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    content = "<binary-or-unreadable>"
                # 组合根只需要前置模块的导入和公开符号；把所有实现正文塞进
                # 单次提示会显著增加延迟，并诱发模型一直阅读而不落盘。
                if self._needs_symbol_summary(context, relative):
                    content = self._symbol_summary(content, relative)
                else:
                    content = content[:20_000]
                sections.extend([f"## {relative}", "```text", content, "```", ""])
            return "\n".join(sections)
        try:
            return self._artifacts.load_ref(ref)
        except (FileNotFoundError, ValueError) as error:
            # Historical Architecture/Contract outputs occasionally used a
            # semantic alias (``todo-architecture-module-*``) instead of the
            # canonical ``architecture`` artifact key.  The compiler now
            # normalizes new plans, while this bounded fallback keeps already
            # persisted traces resumable without granting access to arbitrary
            # artifacts.
            if (
                ref.layer == "published"
                and ref.artifact_key.lower().startswith(
                    ("todo-architecture-", "architecture-module-")
                )
            ):
                return self._artifacts.load_ref(ArtifactRef.published("architecture"))
            raise error

    @staticmethod
    def _needs_symbol_summary(context: ExecutionContext, relative: str) -> bool:
        unit = (context.implementation_unit_id or "").lower()
        owned = tuple(context.owned_files)
        return (
            ("interfaces" in unit or "runtime" in unit or "api" in unit)
            and any(path.endswith("/main.py") and path.startswith("backend/") for path in owned)
            and relative.endswith(".py")
        )

    @staticmethod
    def _symbol_summary(content: str, relative: str) -> str:
        """返回导入、顶层定义和签名摘要，保留组合根所需的协作信息。"""
        try:
            tree = ast.parse(content, filename=relative)
        except SyntaxError:
            return content[:4_000]
        lines = [f"# 符号摘要: {relative}"]
        imports: list[str] = []
        symbols: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.extend(ast.unparse(node).splitlines())
            elif isinstance(node, ast.ImportFrom):
                imports.extend(ast.unparse(node).splitlines())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                try:
                    symbols.append(ast.unparse(node).splitlines()[0])
                except (IndexError, ValueError):
                    symbols.append(node.name)
            elif isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                symbols.extend(f"{name} = ..." for name in names)
        if imports:
            lines.append("## imports")
            lines.extend(imports)
        if symbols:
            lines.append("## public/top-level symbols")
            lines.extend(symbols)
        return "\n".join(lines)

    def write_staged_file(
        self, context: ExecutionContext, path: str, content: str
    ) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有代码分区节点可以写入 Git task worktree")
        slot = context.slot or ""
        prefix = self._SCOPE_PREFIXES.get(slot, "")
        normalized = self._validate_path(path)
        if prefix and not normalized.startswith(prefix):
            # Agent 可能把分区 worktree 当作当前目录，提交 domain/... 而不是
            # backend/domain/...。只有在补全后的路径仍命中合同授权时才接受。
            candidate = f"{prefix}{normalized}"
            if context.allowed_paths and _matches_any(candidate, context.allowed_paths):
                normalized = candidate
            else:
                raise PermissionError(f"代码分区 '{slot}' 只能写入 {prefix} 下的文件")
        if context.allowed_paths and not _matches_any(normalized, context.allowed_paths):
            raise PermissionError(
                f"实现单元 '{context.implementation_unit_id or context.work_item_id}' "
                f"不能写入未授权路径: {normalized}"
            )
        if context.forbidden_paths and _matches_any(normalized, context.forbidden_paths):
            raise PermissionError(f"路径命中实现单元禁止范围: {normalized}")
        if context.owned_files and not _matches_any(normalized, context.owned_files):
            raise PermissionError(
                f"实现单元 '{context.implementation_unit_id or context.work_item_id}' "
                f"只能写入自己拥有的完整文件: {normalized}"
            )
        if not content.strip():
            raise ValueError("代码文件内容不能为空")

        with self._lock:
            self._progress(context, "file_write_started", path=normalized)
            baseline = self._ensure_baseline(context.trace_id)
            previous = self._load_change_set(context.trace_id, context.work_item_id)
            branch = self._branch_for(previous) if previous else self._create_task(
                context, baseline
            )
            target = Path(branch.worktree_path) / "workspace" / normalized
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.read_text(encoding="utf-8") == content:
                self._progress(context, "file_write_idempotent", path=normalized)
                return f"代码文件未变化，保留 ChangeSet {previous.commit if previous else branch.branch_name}"
            target.write_text(content, encoding="utf-8")
            self._progress(context, "file_write_succeeded", path=normalized, bytes=len(content.encode("utf-8")))
            change = self._git.commit_task(
                branch,
                owned_paths=(
                    f"workspace/{prefix}**" if prefix else "workspace/**",
                ),
                message=f"projectos: {slot} change {context.work_item_id}",
            )
            self._save_change_set(
                replace(change, required_paths=tuple(context.required_paths)), slot=slot
            )
            self._progress(context, "changeset_created", path=normalized, commit=change.commit)
            return f"已提交代码 ChangeSet: {change.commit} ({normalized})"

    @staticmethod
    def _progress(context: ExecutionContext, event: str, **details: object) -> None:
        tracker = context.progress
        if tracker is not None and hasattr(tracker, "update"):
            tracker.update("writing_file" if event.startswith("file_") else "changeset", event, **details)

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

    def refresh_baseline(
        self, trace_id: str, *, integrated_commits: tuple[str, ...] = ()
    ) -> str:
        commit = self._git.freeze_baseline(("workspace",), message="projectos: freeze wave baseline")
        previous: set[str] = set()
        path = self._baseline_path(trace_id)
        if path.is_file():
            try:
                previous.update(str(value) for value in self._read_json(path).get("integrated_commits", []))
            except (OSError, ValueError):
                pass
        self._write_json(
            path,
            {
                "trace_id": trace_id,
                "commit": commit,
                "paths": ["workspace"],
                "kind": "wave",
                "integrated_commits": sorted(previous | set(integrated_commits)),
            },
        )
        return commit

    def integrated_commits(self, trace_id: str) -> frozenset[str]:
        path = self._baseline_path(trace_id)
        if not path.is_file():
            return frozenset()
        try:
            return frozenset(str(value) for value in self._read_json(path).get("integrated_commits", []))
        except (OSError, ValueError):
            return frozenset()

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
            required_paths=tuple(str(item) for item in payload.get("required_paths", ())),
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
        if candidate.name not in GitCodeStagingService._STAGED_FILENAMES and candidate.suffix.lower() not in GitCodeStagingService._STAGED_SUFFIXES:
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


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """同时兼容合同中的 workspace/backend/** 和工具参数中的 backend/...。"""
    candidates = (path, f"workspace/{path}")
    return any(
        fnmatch.fnmatch(candidate, normalized)
        or fnmatch.fnmatch(candidate, normalized.replace("**", "*"))
        for candidate in candidates
        for pattern in patterns
        for normalized in (
            pattern + "**" if pattern.endswith("/") else pattern,
        )
    )

class GitCodeIntegrationService:
    """按授权 ChangeSet 三方合并并发布正式代码。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path).resolve()
        self._staging = GitCodeStagingService(str(self._project_path))
        self._artifacts = ArtifactStore(str(self._project_path))
        self._policy = GitCodeIntegrationPolicy()
        self._lock = RLock()

    def review_evidence(self, context: ExecutionContext) -> str:
        """Return bounded, read-only evidence for the integration reviewer."""
        records: list[dict[str, object]] = []
        for ref in context.input_refs:
            try:
                change = self._staging.load_change_set(context.trace_id, ref.work_item_id or "")
            except FileNotFoundError:
                records.append({"work_item_id": ref.work_item_id, "status": "missing_changeset"})
                continue
            workspace = Path(change.worktree_path) / "workspace"
            files: list[dict[str, str]] = []
            for changed in change.changed_files:
                relative = changed.removeprefix("workspace/")
                path = workspace / relative
                record = {"path": relative}
                try:
                    if path.is_file() and path.stat().st_size <= 12_000:
                        record["excerpt"] = path.read_text(encoding="utf-8")[:2_000]
                except (OSError, UnicodeDecodeError):
                    record["excerpt"] = "<unreadable>"
                files.append(record)
            records.append({
                "work_item_id": change.work_item_id,
                "slot": ref.slot,
                "changed_files": list(change.changed_files),
                "required_paths": list(change.required_paths),
                "files": files,
            })
        return json.dumps({"trace_id": context.trace_id, "changesets": records}, ensure_ascii=False)

    def record_review(self, context: ExecutionContext, review: object) -> None:
        path = (
            self._project_path / ".projectos" / "runs" / context.trace_id
            / "integration-review.json"
        )
        payload = {
            "trace_id": context.trace_id,
            "work_item_id": context.work_item_id,
            "verdict": str(getattr(review, "verdict", "")),
            "rationale": str(getattr(review, "rationale", "")),
            "findings": [
                item.as_dict() if hasattr(item, "as_dict") else str(item)
                for item in getattr(review, "findings", ())
            ],
            "adapter_requests": list(getattr(review, "adapter_requests", ())),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def integrate(
        self,
        context: ExecutionContext,
        *,
        review: object | None = None,
        publish_summary: bool = True,
        refresh_baseline: bool = False,
    ) -> str:
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
            baseline = self._staging.load_baseline(context.trace_id)
            pending_outputs: list[tuple[ArtifactRef, ChangeSet]] = []
            for ref, change in outputs_list:
                if change.base_commit == baseline:
                    pending_outputs.append((ref, change))
                elif (
                    change.commit in self._staging.integrated_commits(context.trace_id)
                    or self._staging.git.is_ancestor(change.commit, baseline)
                ):
                    # This ChangeSet was already published by a completed Wave.
                    continue
                else:
                    # A resumed run may have created this committed task
                    # branch before a preceding Wave refreshed the baseline.
                    # Git's three-way merge is the authoritative conflict
                    # check; rejecting solely on the recorded base_commit
                    # strands otherwise independent files after recovery.
                    # Keep the ChangeSet pending and let merge_task report a
                    # real conflict if one exists.
                    pending_outputs.append((ref, change))
            outputs = tuple(pending_outputs)
            if not outputs and not missing_refs:
                if publish_summary and not self._artifacts.exists("implementation"):
                    self._artifacts.save(
                        "implementation",
                        "# 实现摘要\n\n## Git 交付记录\n\n所有代码 ChangeSet 已在前置 Wave 完成确定性合并。\n",
                    )
                return "当前输入中的 ChangeSet 已全部在前置 Wave 合并，无需重复发布。"
            report = self._policy.evaluate(
                outputs,
                missing_refs=tuple(missing_refs),
                common_baseline=baseline,
                is_ancestor=self._staging.git.is_ancestor,
            )
            if not report.passed:
                raise RuntimeError(self._format_policy_error(report))
            if review is not None and getattr(review, "verdict", "") != "approve":
                rationale = str(getattr(review, "rationale", "LLM 审核未批准合并"))
                raise RuntimeError(f"LLM Integration Review 拒绝合并: {rationale}")

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

            import_issues = self._validate_local_imports(integration)
            if import_issues:
                owners = self._owners_for_diagnostics(import_issues)
                raise RuntimeError(
                    "Integration 语义检查拒绝合并（责任方: implementation；请依据失败文件 owner 修复）"
                    + (f" [owner_files: {', '.join(owners)}]" if owners else "")
                    + ": "
                    + "; ".join(import_issues)
                )
            entrypoint_issues = self._validate_contract_entrypoints(
                integration,
                tuple(change for _, change in outputs),
            )
            if entrypoint_issues:
                owners = self._owners_for_diagnostics(entrypoint_issues)
                raise RuntimeError(
                    "Integration 入口契约检查拒绝合并（责任方: implementation）"
                    + (f" [owner_files: {', '.join(owners)}]" if owners else "")
                    + ": "
                    + "; ".join(entrypoint_issues)
                )

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
            if publish_summary:
                self._artifacts.save("implementation", summary)
            if refresh_baseline:
                self._staging.refresh_baseline(
                    context.trace_id,
                    integrated_commits=tuple(change.commit for _, change in outputs),
                )
            return f"Git Policy 通过并发布 {len(files)} 个文件，merge commit: {merge_commit}。"

    @staticmethod
    def _validate_local_imports(integration: TaskBranch) -> tuple[str, ...]:
        """检查已合并 Python 文件引用的本地模块是否真实存在。

        这是文件级 Git 合并之外的最低语义门：它不判断业务正确性，只阻止
        测试或实现引用一个尚未由任何分区提供的本地模块。
        """
        root = Path(integration.worktree_path) / "workspace"
        modules: set[str] = set()
        for source in root.rglob("*.py"):
            relative = source.relative_to(root).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            if not parts:
                continue
            # Python 3 namespace packages do not require __init__.py.  Record
            # every parent package represented by a source file so imports
            # such as ``from app.infrastructure import lifecycle`` are not
            # rejected merely because the directory is namespace-based.
            modules.add(".".join(parts))
            modules.update(".".join(parts[:index]) for index in range(1, len(parts)))
        issues: list[str] = []
        for source in root.rglob("*.py"):
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    module = node.module
                    if module.startswith("backend."):
                        # Keep the package prefix in the module key.  The
                        # module set is built from workspace-relative paths
                        # (for example ``backend.application.dto``), so
                        # stripping ``backend.`` here creates a false missing
                        # module report even when the file is present.
                        candidate = module.replace(".", "/")
                        candidate_module = module
                    elif module.startswith("app."):
                        candidate = "backend/" + module.replace(".", "/")
                        candidate_module = candidate.replace("/", ".")
                    else:
                        continue
                    if candidate_module not in modules and not (root / (candidate + ".py")).is_file():
                        issues.append(f"{source.relative_to(root)} 引用了不存在的本地模块 {module}")
        return tuple(dict.fromkeys(issues))

    def _validate_contract_entrypoints(
        self,
        integration: TaskBranch,
        changes: tuple[ChangeSet, ...],
    ) -> tuple[str, ...]:
        """只在入口所属的当前 Wave 合并时检查入口文件。

        Integration 按 Wave 发布；前置 Wave 不应因后续接口/前端入口尚未
        生成而失败。入口的 owner 由 Implementation Contract 决定。
        """
        from app.domain.architecture.implementation_contract import ProjectContractStore

        try:
            contract = ProjectContractStore(str(self._project_path)).load()
        except (FileNotFoundError, ValueError):
            return ()
        root = Path(integration.worktree_path) / "workspace"
        changed = {
            path.removeprefix("workspace/")
            for change in changes
            for path in change.changed_files
        }
        issues: list[str] = []
        for label, relative in (
            ("backend", contract.entrypoints.backend_file),
            ("frontend", contract.entrypoints.frontend_file),
        ):
            normalized = relative.removeprefix("workspace/") if relative else None
            if not normalized:
                continue
            # Skip the check until the entrypoint's owning unit is part of the
            # current wave.  Once its file is declared/changed, require it in
            # the merged worktree immediately.
            if normalized not in changed:
                continue
            if not (root / normalized).is_file():
                issues.append(f"{label} 入口文件不存在: {relative}")
        return tuple(issues)

    def _owners_for_diagnostics(self, issues: tuple[str, ...]) -> tuple[str, ...]:
        """Map integration diagnostics back to contract-owned files."""
        from app.domain.architecture.implementation_contract import ProjectContractStore

        try:
            contract = ProjectContractStore(str(self._project_path)).load()
        except (FileNotFoundError, ValueError):
            return ()
        paths: list[str] = []
        for issue in issues:
            for unit in contract.units:
                candidates = (*unit.owned_files, *unit.required_paths)
                if any(path in issue or path.removesuffix(".py") in issue for path in candidates):
                    paths.extend(candidates)
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _format_policy_error(report: QualityReport) -> str:
        targets = {
            "code.changeset_available": "implementation",
            "code.files_required": "implementation",
            "code.required_path_missing": "implementation",
            "code.required_path_not_changed": "implementation",
            "code.python_source_invalid": "implementation",
            "code.scope_boundary": "architecture_contract",
            "code.duplicate_path": "architecture_contract",
            "code.common_baseline_required": "integration",
            "code.invalid_source_ref": "architecture_contract",
        }
        owners = sorted({targets.get(issue.rule_id, "integration") for issue in report.issues})
        details = "；".join(
            f"{issue.rule_id}: {issue.summary}" for issue in report.issues
        )
        return f"Policy '{report.policy_id}' 拒绝代码合并（责任方: {', '.join(owners)}）: {details}"

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

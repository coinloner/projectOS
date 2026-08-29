"""ProjectOS 受控 Git 仓库、任务分支和三方合并基础设施。"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from pathlib import Path
import re
import shutil
import subprocess
from typing import Protocol


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class GitExecutor(Protocol):
    def run(self, command: list[str], *, cwd: Path) -> GitCommandResult:
        """执行固定 Git 适配器使用的命令。"""


class SubprocessGitExecutor:
    """唯一调用 Git CLI 的低层适配器，不接受 Agent 文本命令。"""

    def run(self, command: list[str], *, cwd: Path) -> GitCommandResult:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return GitCommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


@dataclass(frozen=True)
class TaskBranch:
    """一个 WorkItem 对应的受控 Git 分支和 worktree。"""

    trace_id: str
    work_item_id: str
    branch_name: str
    base_commit: str
    worktree_path: str


@dataclass(frozen=True)
class ChangeSet:
    """Agent 在任务分支上提交的可追溯变更集合。"""

    trace_id: str
    work_item_id: str
    branch_name: str
    base_commit: str
    commit: str
    changed_files: tuple[str, ...]
    worktree_path: str
    required_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class MergeConflict:
    """Git 无法自动合并的文件冲突。"""

    path: str
    reason: str = "git_unmerged_path"


@dataclass(frozen=True)
class MergeResult:
    """一次三方合并的结构化结果。"""

    merged: bool
    integration_branch: str
    commit: str | None
    conflicts: tuple[MergeConflict, ...] = ()
    message: str | None = None


class GitRepositoryError(RuntimeError):
    """受控 Git 操作失败。"""


class GitRepositoryManager:
    """控制 ProjectOS 仓库生命周期，不向 Agent 暴露任意 Git 能力。

    当前 manager 只负责 Git 基础设施；现有 staged workspace 仍由旧仓库实现。
    后续接入 Code domain 时，CodeStagingService 将通过这里创建 worktree、提交
    ChangeSet，并由 Integration 层调用 merge_task。
    """

    _PROJECTOS_EXCLUDE = ".projectos/worktrees/"

    def __init__(
        self,
        project_path: str,
        *,
        executor: GitExecutor | None = None,
        worktrees_root: str | None = None,
    ) -> None:
        self._root = Path(project_path).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._executor = executor or SubprocessGitExecutor()
        self._worktrees_root = (
            Path(worktrees_root).resolve()
            if worktrees_root is not None
            else self._root / ".projectos" / "worktrees"
        )

    @property
    def repository_path(self) -> Path:
        return self._root

    @property
    def worktrees_root(self) -> Path:
        return self._worktrees_root

    def initialize(self) -> None:
        """初始化仓库并关闭仓库 hooks，避免执行项目提供的任意 hook。"""
        check = self._run(["rev-parse", "--show-toplevel"], check=False)
        current_root = Path(check.stdout.strip()).resolve() if check.returncode == 0 and check.stdout.strip() else None
        # 生成项目可能位于 ProjectOS 自身仓库的 projects/ 子目录中。不能
        # 复用父仓库，否则父级 .gitignore 会阻止 workspace 建立 baseline，
        # 也会让多个项目共享同一个 Git 历史。
        if current_root != self._root:
            self._run(["init", "--quiet"])
        self._run(["config", "user.name", "ProjectOS"])
        self._run(["config", "user.email", "projectos@localhost"])
        self._run(["config", "core.hooksPath", "/dev/null"])
        self._exclude_projectos_worktrees()

    def current_commit(self) -> str:
        self.initialize()
        result = self._run(["rev-parse", "HEAD"], check=False)
        if result.returncode != 0:
            raise GitRepositoryError("仓库还没有可用的 commit")
        return result.stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        self.initialize()
        return self._run(["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0

    def freeze_baseline(
        self,
        paths: tuple[str, ...],
        *,
        message: str = "projectos: freeze baseline",
    ) -> str:
        """将明确列出的项目文件冻结为后续任务共同基线。"""
        self.initialize()
        normalized = self._validate_paths(paths)
        self._run(["add", "--", *normalized])
        staged = self._run(["diff", "--cached", "--quiet"], check=False)
        if staged.returncode == 0:
            head = self._run(["rev-parse", "HEAD"], check=False)
            if head.returncode == 0:
                return head.stdout.strip()
            # A new project may have an empty workspace.  It still needs a
            # stable commit so every task branch has the same three-way base.
            self._run(["commit", "--allow-empty", "--no-verify", "-m", message])
            return self.current_commit()
        self._run(["commit", "--no-verify", "-m", message])
        return self.current_commit()

    def create_task_worktree(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        base_commit: str | None = None,
    ) -> TaskBranch:
        self.initialize()
        base = base_commit or self.current_commit()
        return self._create_worktree(
            trace_id=trace_id,
            work_item_id=work_item_id,
            branch_kind="task",
            base_commit=base,
        )

    def create_integration_worktree(
        self,
        *,
        trace_id: str,
        base_commit: str | None = None,
    ) -> TaskBranch:
        self.initialize()
        base = base_commit or self.current_commit()
        return self._create_worktree(
            trace_id=trace_id,
            work_item_id="integration",
            branch_kind="integration",
            base_commit=base,
        )

    def commit_task(
        self,
        branch: TaskBranch,
        *,
        owned_paths: tuple[str, ...],
        message: str,
    ) -> ChangeSet:
        """只提交该任务声明拥有的变更文件，拒绝越权文件。"""
        worktree = self._validate_worktree(branch)
        changed = self._changed_files(worktree)
        if not changed:
            raise GitRepositoryError(f"任务 '{branch.work_item_id}' 没有产生文件变更")
        self._validate_owned_paths(changed, owned_paths)
        self._run_in(worktree, ["add", "-A", "--", *changed])
        self._run_in(worktree, ["commit", "--no-verify", "-m", message])
        commit = self._run_in(worktree, ["rev-parse", "HEAD"]).stdout.strip()
        changed_since_base = self.changed_files_between(
            branch.base_commit, commit, worktree=worktree
        )
        return ChangeSet(
            trace_id=branch.trace_id,
            work_item_id=branch.work_item_id,
            branch_name=branch.branch_name,
            base_commit=branch.base_commit,
            commit=commit,
            changed_files=changed_since_base,
            worktree_path=str(worktree),
        )

    def changed_files_between(
        self,
        base_commit: str,
        commit: str,
        *,
        worktree: Path | None = None,
    ) -> tuple[str, ...]:
        """返回两个提交之间的文件集合，供 ChangeSet 和 Policy 使用。"""
        cwd = worktree or self._root
        result = self._run_in(cwd, ["diff", "--name-only", base_commit, commit, "--"])
        return tuple(
            sorted(
                _validate_relative_path(path)
                for path in result.stdout.splitlines()
                if path.strip()
            )
        )

    def promote_worktree_changes(
        self,
        integration: TaskBranch,
        *,
        base_commit: str,
        commit: str,
        allowed_prefix: str = "workspace/",
    ) -> tuple[str, ...]:
        """将已提交的 Integration worktree 变更发布到正式 workspace。

        发布是控制面动作，只复制 ``base_commit..commit`` 中的 workspace 文件，
        因此 Agent 永远不能通过此接口覆盖 ProjectOS 自身文件。
        """
        worktree = self._validate_worktree(integration)
        prefix = _validate_relative_path(allowed_prefix.rstrip("/")) + "/"
        result = self._run_in(
            worktree,
            ["diff", "--name-status", "--find-renames", base_commit, commit, "--", prefix],
        )
        published: set[str] = set()
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            status = fields[0]
            if status.startswith("R") and len(fields) == 3:
                old_path, new_path = fields[1], fields[2]
                self._publish_delete(old_path, prefix)
                self._publish_file(worktree, new_path, prefix)
                published.update((old_path, new_path))
                continue
            if len(fields) != 2:
                raise GitRepositoryError(f"无法解析 Git 发布变更: {line}")
            path = fields[1]
            if status == "D":
                self._publish_delete(path, prefix)
            elif status in {"A", "M", "T"}:
                self._publish_file(worktree, path, prefix)
            else:
                raise GitRepositoryError(f"不支持的 Git 发布状态: {status}")
            published.add(path)
        return tuple(sorted(published))

    def _publish_file(self, worktree: Path, path: str, prefix: str) -> None:
        normalized = _validate_relative_path(path)
        if not normalized.startswith(prefix):
            raise GitRepositoryError(f"发布路径越过允许范围: {normalized}")
        source = (worktree / normalized).resolve()
        target = (self._root / normalized).resolve()
        try:
            source.relative_to(worktree)
            target.relative_to(self._root)
        except ValueError as error:
            raise GitRepositoryError("Git 发布路径越过仓库边界") from error
        if not source.is_file():
            raise GitRepositoryError(f"Git 发布源文件不存在: {normalized}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _publish_delete(self, path: str, prefix: str) -> None:
        normalized = _validate_relative_path(path)
        if not normalized.startswith(prefix):
            raise GitRepositoryError(f"删除路径越过允许范围: {normalized}")
        target = (self._root / normalized).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise GitRepositoryError("Git 删除路径越过仓库边界") from error
        if target.is_file() or target.is_symlink():
            target.unlink()

    def merge_task(
        self,
        integration: TaskBranch,
        change_set: ChangeSet,
        *,
        message: str | None = None,
    ) -> MergeResult:
        """将任务分支以三方合并方式合入 Integration worktree。"""
        worktree = self._validate_worktree(integration)
        result = self._run_in(
            worktree,
            ["merge", "--no-commit", "--no-ff", "--no-verify", change_set.branch_name],
            check=False,
        )
        if result.returncode != 0:
            conflicts = tuple(
                MergeConflict(path=path) for path in self._unmerged_files(worktree)
            )
            if not conflicts:
                raise GitRepositoryError(
                    result.stderr.strip() or "Git merge 失败，但没有返回冲突文件"
                )
            return MergeResult(
                merged=False,
                integration_branch=integration.branch_name,
                commit=None,
                conflicts=conflicts,
                message=result.stderr.strip() or "存在需要解决的 Git 冲突",
            )

        if self._run_in(worktree, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
            commit = self._run_in(worktree, ["rev-parse", "HEAD"]).stdout.strip()
            return MergeResult(
                merged=True,
                integration_branch=integration.branch_name,
                commit=commit,
                message="分支已经包含在 Integration 分支中",
            )

        self._run_in(
            worktree,
            [
                "commit",
                "--no-verify",
                "-m",
                message or f"projectos: merge {change_set.work_item_id}",
            ],
        )
        commit = self._run_in(worktree, ["rev-parse", "HEAD"]).stdout.strip()
        return MergeResult(
            merged=True,
            integration_branch=integration.branch_name,
            commit=commit,
        )

    def abort_merge(self, integration: TaskBranch) -> None:
        worktree = self._validate_worktree(integration)
        self._run_in(worktree, ["merge", "--abort"])

    def commit_resolved_merge(
        self,
        integration: TaskBranch,
        *,
        message: str,
    ) -> str:
        """提交 IntegrationAgent 已解决的冲突；未解决状态下拒绝提交。"""
        worktree = self._validate_worktree(integration)
        self._run_in(worktree, ["add", "-A", "--", "."])
        conflicts = self._unmerged_files(worktree)
        if conflicts:
            raise GitRepositoryError(
                "仍存在未解决冲突: " + ", ".join(conflicts)
            )
        self._run_in(worktree, ["commit", "--no-verify", "-m", message])
        return self._run_in(worktree, ["rev-parse", "HEAD"]).stdout.strip()

    def remove_worktree(self, branch: TaskBranch, *, force: bool = False) -> None:
        """由控制面清理已完成任务的 worktree，不删除 Git 分支。"""
        worktree = self._validate_worktree(branch)
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree))
        self._run(args)

    def _create_worktree(
        self,
        *,
        trace_id: str,
        work_item_id: str,
        branch_kind: str,
        base_commit: str,
    ) -> TaskBranch:
        safe_trace = _safe_ref_part(trace_id)
        safe_item = _safe_ref_part(work_item_id)
        branch_name = f"projectos/{safe_trace}/{safe_item}"
        worktree = self._worktrees_root / safe_trace / safe_item
        if worktree.exists():
            current_branch = self._run_in(
                worktree, ["branch", "--show-current"], check=False
            )
            if current_branch.returncode != 0 or current_branch.stdout.strip() != branch_name:
                raise GitRepositoryError(
                    f"受控 worktree 已存在但分支不匹配: {worktree}"
                )
            return TaskBranch(
                trace_id=trace_id,
                work_item_id=work_item_id,
                branch_name=branch_name,
                base_commit=base_commit,
                worktree_path=str(worktree),
            )
        self._worktrees_root.mkdir(parents=True, exist_ok=True)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = self._run(
            ["show-ref", "--verify", f"refs/heads/{branch_name}"], check=False
        ).returncode == 0
        if branch_exists:
            self._run(["worktree", "add", "--quiet", str(worktree), branch_name])
        else:
            self._run(
                [
                    "worktree",
                    "add",
                    "--quiet",
                    "-b",
                    branch_name,
                    str(worktree),
                    base_commit,
                ]
            )
        return TaskBranch(
            trace_id=trace_id,
            work_item_id=work_item_id,
            branch_name=branch_name,
            base_commit=base_commit,
            worktree_path=str(worktree),
        )

    def _validate_worktree(self, branch: TaskBranch) -> Path:
        worktree = Path(branch.worktree_path).resolve()
        try:
            worktree.relative_to(self._worktrees_root)
        except ValueError as error:
            raise GitRepositoryError("worktree 必须位于 ProjectOS 管理目录") from error
        if not worktree.is_dir():
            raise GitRepositoryError(f"worktree 不存在: {worktree}")
        return worktree

    def _changed_files(self, worktree: Path) -> list[str]:
        result = self._run_in(
            worktree,
            ["status", "--porcelain=v1", "--untracked-files=all"],
        )
        paths: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.append(_validate_relative_path(path))
        return sorted(set(paths))

    def _unmerged_files(self, worktree: Path) -> list[str]:
        result = self._run_in(
            worktree,
            ["diff", "--name-only", "--diff-filter=U"],
            check=False,
        )
        if result.returncode not in (0, 1):
            raise GitRepositoryError(result.stderr.strip() or "无法读取 Git 冲突状态")
        return sorted(
            {_validate_relative_path(path) for path in result.stdout.splitlines() if path.strip()}
        )

    @staticmethod
    def _validate_owned_paths(changed: list[str], owned_paths: tuple[str, ...]) -> None:
        if not owned_paths:
            raise GitRepositoryError("任务没有声明 owned_paths")
        for path in changed:
            if not any(
                fnmatch.fnmatch(path, pattern)
                or path.startswith(pattern.rstrip("/") + "/")
                for pattern in owned_paths
            ):
                raise GitRepositoryError(
                    f"任务变更越过 owned_paths: {path}；允许范围: {', '.join(owned_paths)}"
                )

    def _validate_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        if not paths:
            raise GitRepositoryError("基线至少需要一个文件路径")
        normalized = tuple(_validate_relative_path(path) for path in paths)
        for path in normalized:
            if not (self._root / path).exists():
                raise GitRepositoryError(f"基线路径不存在: {path}")
        return normalized

    def _exclude_projectos_worktrees(self) -> None:
        result = self._run(["rev-parse", "--git-path", "info/exclude"])
        exclude = Path(result.stdout.strip())
        if not exclude.is_absolute():
            exclude = self._root / exclude
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if self._PROJECTOS_EXCLUDE not in current.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            suffix = "" if not current or current.endswith("\n") else "\n"
            exclude.write_text(
                current + suffix + self._PROJECTOS_EXCLUDE + "\n",
                encoding="utf-8",
            )

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> GitCommandResult:
        return self._run_in(self._root, args, check=check)

    def _run_in(
        self,
        cwd: Path,
        args: list[str],
        *,
        check: bool = True,
    ) -> GitCommandResult:
        result = self._executor.run(["git", *args], cwd=cwd)
        if check and result.returncode != 0:
            raise GitRepositoryError(
                f"Git 命令失败: git {' '.join(args)}\n"
                + (result.stderr.strip() or result.stdout.strip())
            )
        return result


def _safe_ref_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if not cleaned:
        raise GitRepositoryError("Git 分支标识不能为空")
    return cleaned[:80]


def _validate_relative_path(path: str) -> str:
    candidate = Path(path)
    if (
        not path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or path.startswith("-")
    ):
        raise GitRepositoryError(f"Git 路径必须是安全的相对路径: {path}")
    return candidate.as_posix()

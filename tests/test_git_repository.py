import tempfile
import unittest
from pathlib import Path

from app.workspace.git_repository import GitRepositoryError, GitRepositoryManager


class GitRepositoryManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        (self.root / "workspace").mkdir()
        (self.root / "workspace" / "README.md").write_text("base\n", encoding="utf-8")
        (self.root / "workspace" / "shared.txt").write_text("base\n", encoding="utf-8")
        self.manager = GitRepositoryManager(str(self.root))
        self.manager.initialize()
        self.base = self.manager.freeze_baseline(("workspace",))

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_task_branch_commits_only_owned_files_and_can_merge(self) -> None:
        task = self.manager.create_task_worktree(
            trace_id="tr-001", work_item_id="backend-create", base_commit=self.base
        )
        (Path(task.worktree_path) / "workspace" / "create.py").write_text(
            "created = True\n", encoding="utf-8"
        )
        change = self.manager.commit_task(
            task,
            owned_paths=("workspace/create.py",),
            message="projectos: backend create",
        )

        integration = self.manager.create_integration_worktree(
            trace_id="tr-001", base_commit=self.base
        )
        result = self.manager.merge_task(integration, change)

        self.assertTrue(result.merged)
        self.assertIsNotNone(result.commit)
        self.assertTrue(
            (Path(integration.worktree_path) / "workspace" / "create.py").is_file()
        )
        self.assertEqual(change.base_commit, self.base)

    def test_commit_rejects_change_outside_declared_scope(self) -> None:
        task = self.manager.create_task_worktree(
            trace_id="tr-001", work_item_id="backend-create", base_commit=self.base
        )
        (Path(task.worktree_path) / "workspace" / "README.md").write_text(
            "changed\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(GitRepositoryError, "owned_paths"):
            self.manager.commit_task(
                task,
                owned_paths=("workspace/create.py",),
                message="should be rejected",
            )

    def test_merge_returns_conflict_without_swallowing_it(self) -> None:
        first = self.manager.create_task_worktree(
            trace_id="tr-001", work_item_id="first", base_commit=self.base
        )
        second = self.manager.create_task_worktree(
            trace_id="tr-001", work_item_id="second", base_commit=self.base
        )
        for branch, content in ((first, "first\n"), (second, "second\n")):
            (Path(branch.worktree_path) / "workspace" / "shared.txt").write_text(
                content, encoding="utf-8"
            )
        first_change = self.manager.commit_task(
            first,
            owned_paths=("workspace/shared.txt",),
            message="projectos: first change",
        )
        second_change = self.manager.commit_task(
            second,
            owned_paths=("workspace/shared.txt",),
            message="projectos: second change",
        )

        integration = self.manager.create_integration_worktree(
            trace_id="tr-001", base_commit=self.base
        )
        self.assertTrue(self.manager.merge_task(integration, first_change).merged)
        conflict = self.manager.merge_task(integration, second_change)

        self.assertFalse(conflict.merged)
        self.assertEqual(
            [item.path for item in conflict.conflicts], ["workspace/shared.txt"]
        )
        (Path(integration.worktree_path) / "workspace" / "shared.txt").write_text(
            "resolved\n", encoding="utf-8"
        )
        resolved_commit = self.manager.commit_resolved_merge(
            integration, message="projectos: resolve shared change"
        )
        self.assertTrue(resolved_commit)

    def test_managed_worktree_creation_is_idempotent_for_retries(self) -> None:
        first = self.manager.create_integration_worktree(
            trace_id="tr-001", base_commit=self.base
        )
        second = self.manager.create_integration_worktree(
            trace_id="tr-001", base_commit=self.base
        )

        self.assertEqual(first.branch_name, second.branch_name)
        self.assertEqual(first.worktree_path, second.worktree_path)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef
from app.domain.code.service import CodeIntegrationService, CodeStagingService
from app.execution_context import ExecutionContext, ExecutionMode


class CodeGitIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        (Path(self.project_path) / "workspace").mkdir()
        (Path(self.project_path) / "workspace" / "README.md").write_text(
            "generated project\n", encoding="utf-8"
        )
        self.staging = CodeStagingService(self.project_path)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_partition_writes_are_committed_but_not_published_until_integration(self) -> None:
        backend_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-backend",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            output_slot="backend",
        )
        frontend_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-frontend",
            agent_id="code_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            output_slot="frontend",
        )

        first = self.staging.write_staged_file(
            backend_context, "backend/app.py", "print('first')\n"
        )
        second = self.staging.write_staged_file(
            backend_context, "backend/routes.py", "ROUTES = []\n"
        )
        self.staging.write_staged_file(
            frontend_context, "frontend/index.html", "<main>Todo</main>\n"
        )

        self.assertIn("ChangeSet", first)
        self.assertIn("ChangeSet", second)
        self.assertFalse(
            (Path(self.project_path) / "workspace" / "backend" / "app.py").exists()
        )
        backend_change = self.staging.load_change_set("trace-code", "code-backend")
        self.assertEqual(
            backend_change.changed_files,
            ("workspace/backend/app.py", "workspace/backend/routes.py"),
        )
        self.assertEqual(
            backend_change.base_commit,
            self.staging.load_baseline("trace-code"),
        )

        integration_context = ExecutionContext(
            trace_id="trace-code",
            work_item_id="code-integration",
            agent_id="code_integration_agent",
            execution_mode=ExecutionMode.INTEGRATION,
            input_refs=(
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-code",
                    work_item_id="code-backend",
                    slot="backend",
                ),
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-code",
                    work_item_id="code-frontend",
                    slot="frontend",
                ),
            ),
            publish_target="workspace",
        )
        result = CodeIntegrationService(self.project_path).integrate(integration_context)

        self.assertIn("发布 3 个文件", result)
        self.assertTrue(
            (Path(self.project_path) / "workspace" / "backend" / "app.py").is_file()
        )
        self.assertTrue(
            (Path(self.project_path) / "workspace" / "frontend" / "index.html").is_file()
        )
        summary = (Path(self.project_path) / "implementation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Git 交付记录", summary)
        self.assertIn("workspace/backend/app.py", summary)

    def test_policy_rejects_missing_frontend_before_creating_integration_worktree(self) -> None:
        context = ExecutionContext(
            trace_id="trace-policy",
            work_item_id="code-integration",
            agent_id="code_integration_agent",
            execution_mode=ExecutionMode.INTEGRATION,
            input_refs=(
                ArtifactRef.staged(
                    artifact_key="implementation",
                    trace_id="trace-policy",
                    work_item_id="code-backend",
                    slot="backend",
                ),
            ),
            publish_target="workspace",
        )
        with self.assertRaisesRegex(RuntimeError, "frontend_scope_required"):
            CodeIntegrationService(self.project_path).integrate(context)
        self.assertFalse(
            (Path(self.project_path) / "workspace" / "backend").exists()
        )


if __name__ == "__main__":
    unittest.main()

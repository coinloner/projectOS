import tempfile
import unittest
from pathlib import Path

from app.artifact.store import ArtifactStore
from app.artifact.repository import ArtifactRef
from app.domain.architecture.service import ArchitectureService
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.execution_context import ExecutionContext, ExecutionMode
from app.domain.bootstrap.service import BootstrapService
from app.domain.code.service import CodeService
from app.domain.requirement.service import RequirementService
from app.domain.requirement.tools import RequirementToolSet
from app.domain.review.service import ReviewService
from app.domain.task.service import TaskArtifactWorkflow
from app.domain.test.service import TestService
from app.runtime.manifest import RuntimeManifest


class DomainServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        self.store = ArtifactStore(self.project_path)
        self.store.save("requirement", "# Requirement")
        self.store.save("architecture", "# Architecture")
        self.store.save("tasks", "# Tasks")
        self.store.save("implementation", "# Implementation")
        self.store.save("tests", "# Tests")
        self.store.save("environment", "# Environment")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_requirement_service_and_agent_adapter_are_separate_layers(self) -> None:
        (Path(self.project_path) / "requirement.md").unlink()
        service = RequirementService(self.project_path)
        tools = RequirementToolSet(service)

        self.assertEqual(tools.load_requirement(), "（尚未创建 requirement.md）")
        self.assertEqual(
            tools.save_requirement("# New requirement"), "已保存 requirement.md"
        )
        self.assertEqual(service.load_requirement(), "# New requirement")

    def test_architecture_and_task_services_enforce_their_artifact_contracts(self) -> None:
        architecture = ArchitectureService(self.project_path)
        task = TaskArtifactWorkflow(self.project_path)

        self.assertEqual(architecture.load_artifact("requirement"), "# Requirement")
        self.assertEqual(architecture.save_architecture("# Updated"), "已保存 architecture.md")
        context = ExecutionContext(
            trace_id="tr-task",
            work_item_id="tasks-plan",
            agent_id="task_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            input_refs=(ArtifactRef.published("architecture"),),
            slot="plan",
        )
        self.assertEqual(
            task.load_input(context, "published:architecture:current"), "# Updated"
        )
        self.assertIn("已写入任务暂存输出", task.write_staged(context, "# Updated tasks"))
        with self.assertRaises(PermissionError):
            architecture.load_artifact("tasks")

    def test_architecture_staging_rejects_an_oversized_scope_package(self) -> None:
        workflow = ArchitectureArtifactWorkflow(self.project_path)
        context = ExecutionContext(
            trace_id="tr-architecture", work_item_id="architecture-api",
            agent_id="architecture_agent", execution_mode=ExecutionMode.PARTITIONED,
            slot="api",
        )

        with self.assertRaisesRegex(ValueError, "字符上限"):
            workflow.write_staged(context, "A" * 4201)

    def test_code_test_and_review_services_keep_distinct_workspace_permissions(self) -> None:
        code = CodeService(self.project_path)
        test = TestService(self.project_path)
        review = ReviewService(self.project_path)

        code.write_workspace_file("src/app.py", "VALUE = 1\n")
        test.write_test_file("tests/test_app.py", "import unittest\n")

        self.assertIn("src/app.py", code.list_workspace_files())
        self.assertIn("VALUE = 1", test.read_workspace_file("src/app.py"))
        self.assertIn("VALUE = 1", review.read_workspace_file("src/app.py"))
        with self.assertRaises(PermissionError):
            test.write_test_file("src/app.py", "VALUE = 2\n")
        with self.assertRaises(PermissionError):
            code.write_workspace_file("tests/planted.py", "x = 1\n")
        self.assertEqual(code.load_artifact("environment"), "# Environment")
        self.assertEqual(test.load_artifact("environment"), "# Environment")
        self.assertEqual(review.load_artifact("environment"), "# Environment")
        self.assertEqual(review.save_review("# Review"), "已保存 review.md")
        self.assertEqual(
            (Path(self.project_path) / "review.md").read_text(encoding="utf-8"),
            "# Review",
        )

    def test_review_persists_deterministic_quality_report_for_runtime_projects(self) -> None:
        RuntimeManifest(version=1, profile="python-stdlib").save(self.project_path)
        root = Path(self.project_path)
        (root / "start.sh").write_text("curl /runtime/runs", encoding="utf-8")
        (root / "start.ps1").write_text("Invoke-RestMethod /runtime/runs", encoding="utf-8")
        (root / "workspace" / "backend" / "app").mkdir(parents=True)
        (root / "workspace" / "backend" / "app" / "main.py").write_text("print('x')\n", encoding="utf-8")
        review = ReviewService(self.project_path)
        review.save_review("# Review")
        saved = (root / "review.md").read_text(encoding="utf-8")
        self.assertIn("## 确定性质量策略", saved)
        self.assertIn("project.backend_layers_required", saved)

    def test_bootstrap_service_declares_runtime_without_executing_dependencies(self) -> None:
        bootstrap = BootstrapService(self.project_path)

        result = bootstrap.configure_runtime("python-pip", "example==1.0.0")

        self.assertIn("python-pip", result)
        self.assertIn("dependencies=present", bootstrap.inspect_runtime())
        self.assertFalse((Path(self.project_path) / ".sandbox").exists())

    def test_bootstrap_service_declares_whitelisted_application(self) -> None:
        bootstrap = BootstrapService(self.project_path)

        result = bootstrap.configure_runtime(
            "python-stdlib", application="static-web"
        )

        self.assertIn("static-web", result)
        manifest = RuntimeManifest.load(self.project_path)
        self.assertEqual(manifest.application, "static-web")

    def test_bootstrap_service_rejects_unknown_application(self) -> None:
        bootstrap = BootstrapService(self.project_path)

        with self.assertRaises(ValueError):
            bootstrap.configure_runtime(
                "python-stdlib", application="not-in-catalog"
            )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.artifact.store import ArtifactStore
from app.domain.architecture.service import ArchitectureService
from app.domain.bootstrap.service import BootstrapService
from app.domain.code.service import CodeService
from app.domain.requirement.service import (
    RequirementDocument,
    RequirementService,
)
from app.domain.requirement.tools import RequirementToolSet
from app.domain.review.service import ReviewService
from app.domain.task.service import TaskService
from app.domain.test.service import TestService


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

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_requirement_service_and_agent_adapter_are_separate_layers(self) -> None:
        (Path(self.project_path) / "requirement.md").unlink()
        service = RequirementService(self.project_path)
        tools = RequirementToolSet(service)

        self.assertEqual(tools.load_requirement(), "（尚未创建需求文档）")
        self.assertEqual(
            tools.save_requirement("# New requirement"), "需求文档已保存"
        )
        self.assertEqual(service.load(), RequirementDocument("# New requirement"))

    def test_architecture_and_task_services_enforce_their_artifact_contracts(self) -> None:
        architecture = ArchitectureService(self.project_path)
        task = TaskService(self.project_path)

        self.assertEqual(architecture.load_artifact("requirement"), "# Requirement")
        self.assertEqual(architecture.save_architecture("# Updated"), "已保存 architecture.md")
        self.assertEqual(task.load_artifact("architecture"), "# Updated")
        self.assertEqual(task.save_tasks("# Updated tasks"), "已保存 tasks.md")
        with self.assertRaises(PermissionError):
            architecture.load_artifact("tasks")

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
        self.assertEqual(review.save_review("# Review"), "已保存 review.md")
        self.assertEqual(
            (Path(self.project_path) / "review.md").read_text(encoding="utf-8"),
            "# Review",
        )

    def test_bootstrap_service_declares_runtime_without_executing_dependencies(self) -> None:
        bootstrap = BootstrapService(self.project_path)

        result = bootstrap.configure_runtime("python-pip", "example==1.0.0")

        self.assertIn("python-pip", result)
        self.assertIn("dependencies=present", bootstrap.inspect_runtime())
        self.assertFalse((Path(self.project_path) / ".sandbox").exists())


if __name__ == "__main__":
    unittest.main()

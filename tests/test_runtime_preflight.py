import tempfile
import unittest
from pathlib import Path

from app.policy.quality import ProjectRuntimePreflight, _entrypoint_assembly_issue


class RuntimePreflightTest(unittest.TestCase):
    def test_detects_missing_frontend_reference_and_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "frontend").mkdir(parents=True)
            (workspace / "frontend" / "index.html").write_text(
                '<script type="module" src="/src/main.jsx"></script>', encoding="utf-8"
            )
            (workspace / "docker-compose.yml").write_text(
                "services:\n  backend:\n    build:\n      context: ./backend\n    command: python migrate.py\n",
                encoding="utf-8",
            )
            report = ProjectRuntimePreflight().evaluate(directory)
            codes = {issue.rule_id for issue in report.issues}
            self.assertEqual(codes, {
                "runtime.frontend_reference_missing",
                "runtime.dockerfile_missing",
                "runtime.database_init_missing",
            })

    def test_valid_references_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "frontend" / "src").mkdir(parents=True)
            (workspace / "frontend" / "index.html").write_text(
                '<script type="module" src="/src/main.js"></script>', encoding="utf-8"
            )
            (workspace / "frontend" / "src" / "main.js").write_text("console.log('ok')", encoding="utf-8")
            (workspace / "backend").mkdir()
            (workspace / "backend" / "Dockerfile").write_text("FROM python:3.12-slim", encoding="utf-8")
            (workspace / "backend" / "migrate.py").write_text("print('ok')", encoding="utf-8")
            (workspace / "docker-compose.yml").write_text(
                "services:\n  backend:\n    build:\n      context: ./backend\n    command: python migrate.py\n",
                encoding="utf-8",
            )
            self.assertTrue(ProjectRuntimePreflight().evaluate(directory).passed)

    def test_operations_migration_script_satisfies_database_init_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "backend").mkdir(parents=True)
            (workspace / "operations").mkdir(parents=True)
            (workspace / "operations" / "migrate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "docker-compose.yml").write_text(
                "services:\n  backend:\n    command: sh -c 'python migrate.py && uvicorn app.main:app'\n",
                encoding="utf-8",
            )
            report = ProjectRuntimePreflight().evaluate(directory)
            self.assertNotIn("runtime.database_init_missing", {issue.rule_id for issue in report.issues})

    def test_detects_missing_application_assembly_for_generated_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            app = workspace / "backend" / "app"
            app.mkdir(parents=True)
            (app / "server.py").write_text(
                "def _load_application():\n    return None\n", encoding="utf-8"
            )
            (app / "api.py").write_text("class TodoApi: pass\n", encoding="utf-8")

            issue = _entrypoint_assembly_issue(
                workspace, "backend/app/server.py"
            )

            self.assertIsNotNone(issue)
            self.assertEqual(issue.rule_id, "runtime.application_assembly_missing")

    def test_application_assembly_function_satisfies_runtime_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            app = workspace / "backend" / "app"
            app.mkdir(parents=True)
            (app / "server.py").write_text(
                "def _load_application():\n    return None\n", encoding="utf-8"
            )
            (app / "api.py").write_text(
                "def create_application(domain):\n    return lambda request: None\n",
                encoding="utf-8",
            )

            self.assertIsNone(
                _entrypoint_assembly_issue(workspace, "backend/app/server.py")
            )


if __name__ == "__main__":
    unittest.main()

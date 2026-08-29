import tempfile
import unittest
from pathlib import Path

from app.policy.quality import ProjectQualityPolicy, _evaluate_project_contract
from app.runtime.manifest import RuntimeManifest
from app.runtime.startup import ensure_startup_scripts
from app.domain.architecture.implementation_contract import ImplementationContract, ProjectContractStore


class ProjectQualityPolicyTest(unittest.TestCase):
    @staticmethod
    def _contract(*, required_test_types=()):
        return ImplementationContract.parse({
            "schema_version": 1,
            "entrypoints": {"backend_file": "backend/app/main.py"},
            "layers": ["api", "operations", "domain"],
            "allowed_dependencies": {"api": [], "operations": [], "domain": []},
            "path_mapping": {
                "api": ["backend/app/api/**"],
                "operations": ["backend/app/operations/**"],
                "domain": ["backend/app/domain/**"],
            },
            "required_test_types": list(required_test_types),
            "implementation_units": [{
                "unit_id": "domain-file",
                "layer": "domain",
                "objective": "领域文件",
                "allowed_paths": ["backend/app/domain/**"],
            }],
        })

    def test_architecture_contract_is_validated_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            content = {
                "schema_version": 1,
                "layers": ["api", "domain"],
                "allowed_dependencies": {"api": ["domain"], "domain": []},
                "forbidden_imports": {"api": [], "domain": ["fastapi"]},
                "required_test_types": ["domain_unit"],
                "path_mapping": {"api": ["workspace/backend/api/**"], "domain": ["workspace/backend/domain/**"]},
            }
            import json
            content["implementation_units"] = [{
                "unit_id": "domain-file",
                "layer": "domain",
                "objective": "领域文件",
                "allowed_paths": ["backend/domain/**"],
                "owned_files": ["backend/domain/entities.py"],
            }]
            store = ProjectContractStore(directory)
            store.save(json.dumps(content))
            self.assertEqual(store.load().layers, ("api", "domain"))
    def test_web_backend_without_application_layer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RuntimeManifest(version=1, profile="python-stdlib").save(directory)
            ensure_startup_scripts(directory, project_id="demo")
            app = root / "workspace" / "backend" / "app"
            app.mkdir(parents=True)
            (app / "main.py").write_text("from .routers import api\n", encoding="utf-8")
            (app / "routers").mkdir()
            (app / "database.py").write_text("", encoding="utf-8")
            (app / "models.py").write_text("", encoding="utf-8")
            report = ProjectQualityPolicy().evaluate(directory)
            self.assertFalse(report.passed)
            self.assertTrue(any(issue.rule_id == "project.backend_layers_required" for issue in report.issues))

    def test_four_layer_backend_passes_structural_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            RuntimeManifest(version=1, profile="python-stdlib").save(directory)
            ensure_startup_scripts(directory, project_id="demo")
            app = root / "workspace" / "backend" / "app"
            for name in ("api", "services", "repositories", "domain"):
                (app / name).mkdir(parents=True)
                (app / name / "__init__.py").write_text("", encoding="utf-8")
            (app / "main.py").write_text("from app.api import router\n", encoding="utf-8")
            tests = root / "workspace" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_smoke.py").write_text("", encoding="utf-8")
            self.assertTrue(ProjectQualityPolicy().evaluate(directory).passed)

    def test_declared_entrypoints_and_worker_wrapper_are_effective_layer_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            (workspace / "backend" / "app").mkdir(parents=True)
            (workspace / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
            (workspace / "backend" / "worker.py").write_text("", encoding="utf-8")
            (workspace / "backend" / "extra.py").write_text("", encoding="utf-8")
            issues = _evaluate_project_contract(workspace, self._contract())
            boundaries = [
                issue.summary for issue in issues if issue.rule_id == "project.layer_path_boundary"
            ]
            self.assertEqual(len(boundaries), 1)
            self.assertIn("backend/extra.py", boundaries[0])

    def test_postgresql_and_concurrency_evidence_uses_paths_and_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            test_dir = workspace / "tests" / "infrastructure"
            test_dir.mkdir(parents=True)
            (test_dir / "test_postgresql.py").write_text(
                "import asyncpg\nDATABASE_URL = 'postgresql://example'\n",
                encoding="utf-8",
            )
            concurrency_dir = workspace / "tests" / "concurrency"
            concurrency_dir.mkdir(parents=True)
            (concurrency_dir / "test_inventory.py").write_text(
                "asyncio.gather(worker_a(), worker_b())\n",
                encoding="utf-8",
            )
            issues = _evaluate_project_contract(
                workspace,
                self._contract(required_test_types=("postgresql_integration", "concurrency")),
            )
            self.assertFalse(any(issue.rule_id == "project.required_test_type_missing" for issue in issues))


if __name__ == "__main__":
    unittest.main()

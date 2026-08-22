import tempfile
import unittest
from pathlib import Path

from app.policy.quality import ProjectQualityPolicy
from app.runtime.manifest import RuntimeManifest
from app.runtime.startup import ensure_startup_scripts
from app.domain.architecture.layer_contract import LayerContractStore


class ProjectQualityPolicyTest(unittest.TestCase):
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
            store = LayerContractStore(directory)
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


if __name__ == "__main__":
    unittest.main()

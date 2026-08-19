import tempfile
import unittest
from pathlib import Path

from app.project.project import Project
from app.runtime.manifest import RuntimeManifest


class ProjectRuntimeTest(unittest.TestCase):
    def test_new_project_declares_sandbox_runtime_without_host_virtualenv(self) -> None:
        with tempfile.TemporaryDirectory() as base_dir:
            project = Project(name="sandboxed", base_dir=base_dir)
            project.create()

            manifest = RuntimeManifest.load(str(Path(base_dir) / "sandboxed"))
            self.assertEqual(manifest.profile, "python-stdlib")
            self.assertIsNone(manifest.application)
            self.assertFalse((project.workspace_path / ".venv").exists())


if __name__ == "__main__":
    unittest.main()

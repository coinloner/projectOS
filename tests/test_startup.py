import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.runtime.startup import ensure_startup_scripts


class StartupScriptTest(unittest.TestCase):
    def test_generates_independent_and_managed_cross_platform_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shell, powershell = ensure_startup_scripts(directory, project_id="demo")
            self.assertTrue(shell.is_file())
            self.assertTrue(powershell.is_file())
            self.assertTrue(os.stat(shell).st_mode & stat.S_IXUSR)
            shell_text = shell.read_text(encoding="utf-8")
            self.assertIn("local_runtime.py", shell_text)
            self.assertNotIn("curl --fail", shell_text)
            self.assertNotIn("docker run", shell_text)
            ps_text = powershell.read_text(encoding="utf-8")
            self.assertIn("local_runtime.py", ps_text)
            self.assertNotIn("docker run", ps_text)
            managed = Path(directory) / "start-managed.sh"
            self.assertTrue(managed.is_file())
            self.assertIn("/api/v1/projects/import", managed.read_text(encoding="utf-8"))
            self.assertIn("dependency-approvals/approve", managed.read_text(encoding="utf-8"))
            self.assertTrue((Path(directory) / "start-managed.ps1").is_file())
            self.assertTrue((Path(directory) / "start.command").is_file())
            self.assertTrue((Path(directory) / "docker-compose.yml").is_file())
            self.assertTrue((Path(directory) / ".projectos" / "local_runtime.py").is_file())
            checked = subprocess.run(["bash", "-n", str(shell)], check=False)
            self.assertEqual(checked.returncode, 0)

    def test_local_launcher_is_independent_from_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shell, _ = ensure_startup_scripts(directory, project_id="demo")
            content = shell.read_text(encoding="utf-8")
            self.assertIn("local_runtime.py", content)
            self.assertNotIn("curl --fail", content)
            self.assertTrue(os.stat(Path(directory) / "start.command").st_mode & stat.S_IXUSR)

    def test_generation_is_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = ensure_startup_scripts(directory, project_id="demo")
            before = tuple(path.read_text(encoding="utf-8") for path in first)
            second = ensure_startup_scripts(directory, project_id="demo")
            self.assertEqual(first, second)
            self.assertEqual(before, tuple(path.read_text(encoding="utf-8") for path in second))

    def test_stdlib_compose_consumes_contract_backend_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: python-backend\n",
                encoding="utf-8",
            )
            contract = root / ".projectos" / "architecture"
            contract.mkdir(parents=True)
            (contract / "project-contract.json").write_text(
                '{"entrypoints":{"backend_command":"python -m backend.app.server"}}\n',
                encoding="utf-8",
            )
            ensure_startup_scripts(directory, project_id="demo")
            compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
            self.assertIn("    - python\n    - -m\n    - backend.app.server", compose)
            self.assertIn("./workspace:/workspace:ro", compose)

    def test_stdlib_compose_accepts_safe_module_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: python-backend\n",
                encoding="utf-8",
            )
            contract = root / ".projectos" / "architecture"
            contract.mkdir(parents=True)
            (contract / "project-contract.json").write_text(
                '{"entrypoints":{"backend_command":"python -m runtime.server"}}\n',
                encoding="utf-8",
            )
            ensure_startup_scripts(directory, project_id="demo")
            compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
            self.assertIn("    - runtime.server", compose)


if __name__ == "__main__":
    unittest.main()

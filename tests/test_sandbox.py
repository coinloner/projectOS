import tempfile
import unittest
from pathlib import Path

from app.runtime.manifest import RuntimeManifest
from app.sandbox.controller import SandboxController
from app.sandbox.docker_provider import CommandResult, DockerSandboxProvider
from app.sandbox.policy import SandboxPolicy
from app.sandbox.resolver import DockerDependencyResolver
from app.sandbox.result import SandboxStatus


class FakeDockerExecutor:
    def __init__(self, results: list[CommandResult]) -> None:
        self._results = iter(results)
        self.commands: list[list[str]] = []

    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        self.commands.append(command)
        return next(self._results)


class SandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        (Path(self.project_path) / "workspace" / "tests").mkdir(parents=True)
        (Path(self.project_path) / "workspace" / "tests" / "test_sample.py").write_text(
            "import unittest\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_manifest_rejects_unknown_profile_and_untrusted_dependency_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的 runtime profile"):
            RuntimeManifest.from_dict({"version": 1, "profile": "arbitrary"})
        with self.assertRaisesRegex(ValueError, "dependencies_file"):
            RuntimeManifest.from_dict(
                {
                    "version": 1,
                    "profile": "python-pip",
                    "dependencies_file": "../secrets.txt",
                }
            )

    def test_policy_rejects_untrusted_dependency_declaration(self) -> None:
        root = Path(self.project_path)
        (root / "requirements.in").write_text("https://bad.example/pkg.whl\n", encoding="utf-8")
        manifest = RuntimeManifest.from_dict(
            {
                "version": 1,
                "profile": "python-pip",
                "dependencies_file": "requirements.in",
            }
        )
        with self.assertRaisesRegex(ValueError, "不允许"):
            SandboxPolicy().create_spec(
                project_path=self.project_path,
                manifest=manifest,
                check_id="unit",
            )

    def test_docker_provider_enforces_fixed_isolated_run_command(self) -> None:
        manifest = RuntimeManifest.from_dict(
            {"version": 1, "profile": "python-stdlib"}
        )
        spec = SandboxPolicy().create_spec(
            project_path=self.project_path,
            manifest=manifest,
            check_id="unit",
        )
        executor = FakeDockerExecutor(
            [CommandResult(exit_code=0), CommandResult(exit_code=0, stdout="ok")]
        )

        result = DockerSandboxProvider(executor).run_check(spec)

        self.assertEqual(result.status, SandboxStatus.PASSED)
        command = executor.commands[1]
        self.assertIn("--network", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("65532:65532", command)
        self.assertNotIn("requirements.txt", command)
        self.assertEqual(command[-7:], ["python", "-m", "unittest", "discover", "-s", "tests", "-v"])

    def test_controller_returns_setup_failure_when_runtime_manifest_is_missing(self) -> None:
        result = SandboxController().run_check(self.project_path, "unit")

        self.assertEqual(result.status, SandboxStatus.SETUP_FAILED)
        self.assertIn("缺少运行时声明", result.message)

    def test_dependency_resolver_requires_explicit_owner_approval(self) -> None:
        resolver = DockerDependencyResolver(FakeDockerExecutor([]))

        with self.assertRaisesRegex(PermissionError, "显式批准"):
            resolver.resolve(self.project_path, approved=False)

    def test_dependency_resolver_never_mounts_workspace_into_networked_container(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        (root / "requirements.in").write_text(
            "example==1.0.0\n",
            encoding="utf-8",
        )
        executor = FakeDockerExecutor(
            [CommandResult(exit_code=0), CommandResult(exit_code=0, stdout="done")]
        )

        result = DockerDependencyResolver(executor).resolve(
            self.project_path, approved=True
        )

        self.assertTrue(result.ok)
        self.assertTrue((result.cache_path / ".projectos-ready").is_file())
        command = executor.commands[1]
        self.assertIn("bridge", command)
        self.assertNotIn(str(root / "workspace"), " ".join(command))
        self.assertIn("/input", " ".join(command))
        self.assertIn("/wheels", " ".join(command))


if __name__ == "__main__":
    unittest.main()

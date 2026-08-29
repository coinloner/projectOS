import tempfile
import unittest
from pathlib import Path

from app.runtime.manifest import RuntimeManifest
from app.sandbox.controller import SandboxController
from app.application.environment import EnvironmentProvisioner
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
        self.assertIn("/site-packages:rw,exec,nosuid,nodev,size=256m", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("65532:65532", command)
        self.assertNotIn("requirements.txt", command)
        self.assertEqual(command[-7:], ["python", "-m", "unittest", "discover", "-s", "tests", "-v"])

    def test_web_unit_check_uses_node_image_and_command(self) -> None:
        manifest = RuntimeManifest.from_dict(
            {"version": 1, "profile": "python-stdlib"}
        )
        spec = SandboxPolicy().create_spec(
            project_path=self.project_path,
            manifest=manifest,
            check_id="web-unit",
        )
        self.assertEqual(spec.image, "node:22-alpine")
        executor = FakeDockerExecutor(
            [CommandResult(exit_code=0), CommandResult(exit_code=0, stdout="ok")]
        )

        result = DockerSandboxProvider(executor).run_check(spec)

        self.assertEqual(result.status, SandboxStatus.PASSED)
        command = executor.commands[1]
        self.assertIn("node:22-alpine", command)
        self.assertEqual(command[-2:], ["node", "--test"])

    def test_python_pip_selects_pytest_when_declared(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        (root / "requirements.in").write_text("pytest\n", encoding="utf-8")
        # Policy only requires a digest-matching marker before creating a spec.
        import hashlib

        digest = hashlib.sha256((root / "requirements.in").read_bytes()).hexdigest()
        cache = root / ".sandbox" / "wheels" / digest
        cache.mkdir(parents=True)
        (cache / ".projectos-ready").write_text(digest, encoding="utf-8")

        spec = SandboxPolicy().create_spec(
            project_path=self.project_path,
            manifest=RuntimeManifest.load(self.project_path),
            check_id="unit",
        )

        self.assertEqual(spec.command_override, ("python", "-m", "pytest", "-q"))

    def test_controller_recognises_underscore_frontend_test_name(self) -> None:
        from app.sandbox.controller import _has_web_tests

        frontend_test = Path(self.project_path) / "workspace" / "tests" / "test_frontend.js"
        frontend_test.write_text("test('ok', () => {});\n", encoding="utf-8")
        self.assertTrue(_has_web_tests(self.project_path))
        spec = SandboxPolicy().create_spec(
            project_path=self.project_path,
            manifest=RuntimeManifest.from_dict({"version": 1, "profile": "python-stdlib"}),
            check_id="web-unit",
        )
        self.assertEqual(
            spec.command_override,
            ("node", "--test", "tests/test_frontend.js"),
        )

    def test_policy_rejects_check_id_outside_profile_whitelist(self) -> None:
        manifest = RuntimeManifest.from_dict(
            {"version": 1, "profile": "python-stdlib"}
        )
        with self.assertRaisesRegex(PermissionError, "不允许执行 check"):
            SandboxPolicy().create_spec(
                project_path=self.project_path,
                manifest=manifest,
                check_id="arbitrary-check",
            )

    def test_controller_returns_setup_failure_when_runtime_manifest_is_missing(self) -> None:
        result = SandboxController().run_check(self.project_path, "unit")

        self.assertEqual(result.status, SandboxStatus.SETUP_FAILED)
        self.assertIn("缺少运行时声明", result.message)

    def test_controller_rejects_empty_web_test_suite(self) -> None:
        RuntimeManifest(version=1, profile="python-stdlib").save(self.project_path)
        result = SandboxController().run_check(self.project_path, "web-unit")
        self.assertEqual(result.status, SandboxStatus.FAILED)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("未发现前端 Node 测试文件", result.message)

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
        self.assertNotIn("--require-hashes", command)

    def test_dependency_resolver_retries_in_fresh_container_and_cleans_partial_cache(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(version=1, profile="python-pip", dependencies_file="requirements.in").save(self.project_path)
        (root / "requirements.in").write_text("example==1.0.0\n", encoding="utf-8")
        executor = FakeDockerExecutor([
            CommandResult(exit_code=0),
            CommandResult(exit_code=1, stderr="OSError: [Errno 28] No space left on device"),
            CommandResult(exit_code=0),
        ])
        result = DockerDependencyResolver(executor).resolve(self.project_path, approved=True)
        self.assertTrue(result.ok)
        self.assertEqual(len(executor.commands), 3)
        self.assertIn("--only-binary", executor.commands[2])
        self.assertTrue((result.cache_path / ".projectos-ready").is_file())

    def test_dependency_failure_message_classifies_network_and_storage(self) -> None:
        self.assertIn("网络连接不稳定", DockerDependencyResolver._failure_message(
            CommandResult(exit_code=1, stderr="SSLEOFError: connection aborted")
        ))
        self.assertIn("临时存储空间不足", DockerDependencyResolver._failure_message(
            CommandResult(exit_code=1, stderr="No space left on device")
        ))

    def test_dependency_recovery_uses_host_temp_after_storage_failure(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(version=1, profile="python-pip", dependencies_file="requirements.in").save(self.project_path)
        (root / "requirements.in").write_text("example==1.0.0\n", encoding="utf-8")
        executor = FakeDockerExecutor([
            CommandResult(exit_code=0),
            CommandResult(exit_code=1, stderr="No space left on device"),
            CommandResult(exit_code=0),
        ])
        result = DockerDependencyResolver(executor).resolve(self.project_path, approved=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertIn("host_backed_temp", result.recovery_actions)
        self.assertIn("type=bind", " ".join(executor.commands[2]))
        self.assertIn("/tmp", " ".join(executor.commands[2]))

    def test_dependency_failure_metadata_classifies_incompatible_requirements(self) -> None:
        result = DockerDependencyResolver(FakeDockerExecutor([]))
        self.assertEqual(
            result.classify_failure(CommandResult(exit_code=1, stderr="No matching distribution found" )).value,
            "incompatible",
        )

    def test_environment_provisioner_pulls_allowlisted_image_when_missing(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(version=1, profile="python-stdlib").save(self.project_path)

        class PullingExecutor:
            def __init__(self) -> None:
                self.commands: list[list[str]] = []
                self.available: set[str] = set()

            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                self.commands.append(command)
                if command[1:3] == ["image", "inspect"]:
                    image = command[3]
                    return CommandResult(exit_code=0 if image in self.available else 1, stderr="missing")
                if command[1:3] == ["image", "ls"]:
                    return CommandResult(exit_code=0, stdout="\n".join(sorted(self.available)))
                if command[1] == "pull":
                    self.available.add(command[2])
                    return CommandResult(exit_code=0, stdout="pulled")
                return CommandResult(exit_code=0)

        executor = PullingExecutor()
        result = EnvironmentProvisioner(executor).prepare(self.project_path)

        self.assertTrue(result.ok)
        # profile 主镜像与其全部检查镜像（含 node）都被准备
        self.assertIn(["docker", "pull", "python:3.12-slim"], executor.commands)
        self.assertIn(["docker", "pull", "node:22-alpine"], executor.commands)
        status = EnvironmentProvisioner(executor).status(self.project_path)
        self.assertEqual(status["status"], "ready")

    def test_environment_provisioner_does_not_resolve_dependencies_without_approval(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        (root / "requirements.in").write_text("example==1.0.0\n", encoding="utf-8")

        class ReadyExecutor:
            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                return CommandResult(exit_code=0, stdout="ready")

        result = EnvironmentProvisioner(ReadyExecutor()).prepare(self.project_path)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "dependency_approval_required")

    def test_environment_provisioner_persists_detected_application(self) -> None:
        root = Path(self.project_path)
        (root / "workspace" / "backend" / "app").mkdir(parents=True)
        (root / "workspace" / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
        (root / "workspace" / "backend" / "migrate.py").write_text("", encoding="utf-8")
        (root / "workspace" / "backend" / "seed.py").write_text("", encoding="utf-8")
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        (root / "requirements.in").write_text("fastapi\npsycopg[binary]\n", encoding="utf-8")

        class ReadyExecutor:
            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                return CommandResult(exit_code=0, stdout="ready")

        result = EnvironmentProvisioner(ReadyExecutor()).prepare(self.project_path)

        self.assertEqual(result.status, "dependency_approval_required")
        self.assertEqual(result.application, "fastapi-postgres")
        self.assertEqual(RuntimeManifest.load(self.project_path).application, "fastapi-postgres")

    def test_dependency_approval_prepares_cache_and_is_invalidated_by_requirements_change(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        (root / "requirements.in").write_text("example==1.0.0\n", encoding="utf-8")

        class ReadyExecutor:
            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                if command[1:3] == ["image", "inspect"]:
                    return CommandResult(exit_code=0)
                return CommandResult(exit_code=0, stdout="ready")

        provisioner = EnvironmentProvisioner(ReadyExecutor())
        self.assertEqual(provisioner.dependency_approval(self.project_path)["status"], "pending")
        result = provisioner.approve_dependencies(self.project_path)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(provisioner.dependency_approval(self.project_path)["status"], "approved")
        (root / "requirements.in").write_text("example==2.0.0\n", encoding="utf-8")
        self.assertEqual(provisioner.dependency_approval(self.project_path)["status"], "pending")

    def test_environment_preparation_reuses_ready_dependency_cache(self) -> None:
        root = Path(self.project_path)
        RuntimeManifest(
            version=1,
            profile="python-pip",
            dependencies_file="requirements.in",
        ).save(self.project_path)
        dependencies = root / "requirements.in"
        dependencies.write_text("example==1.0.0\n", encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(dependencies.read_bytes()).hexdigest()
        cache = root / ".sandbox" / "wheels" / digest
        cache.mkdir(parents=True)
        (cache / ".projectos-ready").write_text(digest, encoding="utf-8")

        class CacheOnlyExecutor:
            def __init__(self) -> None:
                self.commands: list[list[str]] = []

            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                self.commands.append(command)
                return CommandResult(exit_code=0, stdout="ready")

        executor = CacheOnlyExecutor()
        result = EnvironmentProvisioner(executor).prepare(self.project_path, dependencies_approved=True)

        self.assertTrue(result.ok)
        self.assertFalse(any("pip" in command for command in executor.commands))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from app.sandbox.application_runner import (
    ApplicationRunError,
    ApplicationRunStatus,
    DockerApplicationRunner,
)
from app.sandbox.docker_provider import CommandResult
from app.runtime.application import ApplicationCatalog
from app.runtime.manifest import RuntimeManifest
from app.runtime.port_allocator import PortAllocator


class FakeDockerExecutor:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        self.commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return CommandResult(0, stdout="image")
        if command[:3] == ["docker", "volume", "create"]:
            return CommandResult(0, stdout=command[-1])
        if "--detach" in command:
            service = command[-1]
            return CommandResult(0, stdout=f"container-{service}\n")
        if command[:3] == ["docker", "inspect", "--format={{.State.Running}}"]:
            return CommandResult(0, stdout="true\n")
        return CommandResult(0)


class ApplicationRunnerTest(unittest.TestCase):
    def test_detects_generated_fastapi_postgres_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace" / "backend" / "app").mkdir(parents=True)
            (root / "workspace" / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
            (root / "workspace" / "backend" / "migrate.py").write_text("", encoding="utf-8")
            (root / "workspace" / "backend" / "seed.py").write_text("", encoding="utf-8")
            (root / "requirements.in").write_text("fastapi\npsycopg[binary]\n", encoding="utf-8")

            self.assertEqual(ApplicationCatalog.detect(str(root)), "fastapi-postgres")
            self.assertEqual(len(ApplicationCatalog.get("fastapi-postgres").services), 2)

    def test_detects_static_web_and_todo_web_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace").mkdir()
            (root / "workspace" / "index.html").write_text("<html></html>", encoding="utf-8")
            self.assertEqual(ApplicationCatalog.detect(str(root)), "static-web")
            profile = ApplicationCatalog.get("static-web")
            self.assertEqual(len(profile.services), 1)
            self.assertEqual(profile.services[0].command[-2:], ("--bind", "0.0.0.0"))
            (root / "workspace" / "backend").mkdir()
            (root / "workspace" / "frontend").mkdir()
            self.assertEqual(ApplicationCatalog.detect(str(root)), "todo-web")

    def test_detects_fastapi_postgres_web_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace" / "backend" / "app").mkdir(parents=True)
            (root / "workspace" / "frontend").mkdir(parents=True)
            (root / "workspace" / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
            (root / "workspace" / "backend" / "migrate.py").write_text("", encoding="utf-8")
            (root / "workspace" / "backend" / "seed.py").write_text("", encoding="utf-8")
            (root / "requirements.in").write_text("fastapi\npsycopg[binary]\n", encoding="utf-8")
            self.assertEqual(ApplicationCatalog.detect(str(root)), "fastapi-postgres-web")
            backend = ApplicationCatalog.get("fastapi-postgres-web").services[1]
            self.assertEqual(backend.workspace_dir, "")
            self.assertEqual(backend.container_workdir, "/workspace/backend")

    def test_detects_fastapi_postgres_web_without_migration_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace" / "backend" / "app").mkdir(parents=True)
            (root / "workspace" / "frontend").mkdir(parents=True)
            (root / "workspace" / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
            (root / "requirements.in").write_text("fastapi\npsycopg2-binary\n", encoding="utf-8")
            self.assertEqual(ApplicationCatalog.detect(str(root)), "fastapi-postgres-web")

    def test_start_static_web_via_declared_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: static-web\n",
                encoding="utf-8",
            )
            (root / "workspace").mkdir()
            (root / "workspace" / "index.html").write_text("<html></html>", encoding="utf-8")
            executor = FakeDockerExecutor()

            result = DockerApplicationRunner(executor).start(str(root))

            self.assertEqual(result.status, ApplicationRunStatus.RUNNING)
            self.assertEqual(result.application_id, "static-web")
            urls = [service.host_url for service in result.services]
            self.assertEqual(len(urls), 1)
            port = int(urls[0].rsplit(":", 1)[-1])
            self.assertGreaterEqual(port, PortAllocator.DEFAULT_RANGE[0])
            self.assertLessEqual(port, PortAllocator.DEFAULT_RANGE[1])
            docker_runs = [command for command in executor.commands if "--detach" in command]
            self.assertEqual(len(docker_runs), 1)
            self.assertIn("http.server", docker_runs[0])
            self.assertIn(f"127.0.0.1:{port}:8081", docker_runs[0])

    def test_start_detects_static_web_without_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\n", encoding="utf-8"
            )
            (root / "workspace").mkdir()
            (root / "workspace" / "index.html").write_text("<html></html>", encoding="utf-8")
            executor = FakeDockerExecutor()

            result = DockerApplicationRunner(executor).start(str(root))

            self.assertEqual(result.application_id, "static-web")
            self.assertEqual(result.status, ApplicationRunStatus.RUNNING)

    def test_fastapi_profile_mounts_dependency_cache_and_database_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workspace" / "backend").mkdir(parents=True)
            (root / "requirements.in").write_text("fastapi\n", encoding="utf-8")
            RuntimeManifest(version=1, profile="python-pip", dependencies_file="requirements.in").save(str(root))
            import hashlib

            digest = hashlib.sha256((root / "requirements.in").read_bytes()).hexdigest()
            cache = root / ".sandbox" / "wheels" / digest
            cache.mkdir(parents=True)
            (cache / ".projectos-ready").write_text(digest, encoding="utf-8")
            service = ApplicationCatalog.get("fastapi-postgres").services[1]

            command = DockerApplicationRunner._docker_run_command(
                "python:3.12-slim",
                service,
                root / "workspace" / "backend",
                "backend-container",
                None,
                "project-network",
                root,
                RuntimeManifest.load(str(root)),
            )

            self.assertIn("--network", command)
            self.assertIn("project-network", command)
            self.assertIn(f"PROJECTOS_DEPENDENCY_DIR=/site-packages/{digest}", command)
            self.assertIn(f"PYTHONPATH=/site-packages/{digest}", command)
            self.assertIn(
                "DATABASE_URL=postgresql://postgres:postgres@db:5432/taskmgmt",
                command,
            )

    def test_start_uses_catalog_and_returns_allocated_service_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: todo-web\n",
                encoding="utf-8",
            )
            (root / "workspace" / "backend").mkdir(parents=True)
            (root / "workspace" / "frontend").mkdir()
            executor = FakeDockerExecutor()

            result = DockerApplicationRunner(executor).start(str(root))

            self.assertEqual(result.status, ApplicationRunStatus.RUNNING)
            urls = [service.host_url for service in result.services]
            self.assertEqual(len(urls), 2)
            host_ports = sorted(int(url.rsplit(":", 1)[-1]) for url in urls)
            self.assertEqual(len(set(host_ports)), 2, "每个服务应拿到不同的端口")
            for port in host_ports:
                self.assertGreaterEqual(port, PortAllocator.DEFAULT_RANGE[0])
                self.assertLessEqual(port, PortAllocator.DEFAULT_RANGE[1])
            docker_runs = [command for command in executor.commands if "--detach" in command]
            self.assertEqual(len(docker_runs), 2)
            publish_args = [
                command[command.index("--publish") + 1]
                for command in docker_runs
                if "--publish" in command
            ]
            self.assertEqual(
                sorted(publish_args),
                [
                    f"127.0.0.1:{host_ports[0]}:8000",
                    f"127.0.0.1:{host_ports[1]}:8080",
                ],
            )
            for command in docker_runs:
                self.assertIn("--read-only", command)
                self.assertIn("--cap-drop", command)
                self.assertIn("ALL", command)
                self.assertIn("--user", command)
                self.assertIn("65532:65532", command)
                self.assertNotIn("--privileged", command)

    def test_failed_start_releases_allocated_ports(self) -> None:
        class FailingNetworkExecutor(FakeDockerExecutor):
            def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
                if command[:3] == ["docker", "network", "create"]:
                    return CommandResult(1, stderr="network boom")
                return super().run(command, timeout_seconds)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: static-web\n",
                encoding="utf-8",
            )
            (root / "workspace").mkdir()
            (root / "workspace" / "index.html").write_text("<html></html>", encoding="utf-8")
            allocator = PortAllocator()
            runner = DockerApplicationRunner(
                FailingNetworkExecutor(), port_allocator=allocator
            )

            with self.assertRaises(ApplicationRunError):
                runner.start(str(root))

            # 启动失败后端口已归还，分配器应能重新给出池中第一个端口
            self.assertEqual(allocator.allocate(1), [PortAllocator.DEFAULT_RANGE[0]])
            allocator.release([PortAllocator.DEFAULT_RANGE[0]])

    def test_stop_releases_ports_for_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: todo-web\n",
                encoding="utf-8",
            )
            (root / "workspace" / "backend").mkdir(parents=True)
            (root / "workspace" / "frontend").mkdir()
            allocator = PortAllocator()
            runner = DockerApplicationRunner(FakeDockerExecutor(), port_allocator=allocator)
            started = runner.start(str(root))
            ports = [
                int(service.host_url.rsplit(":", 1)[-1]) for service in started.services
            ]

            runner.stop(started.run_id)

            self.assertEqual(allocator.allocate(2), sorted(ports))
            allocator.release(sorted(ports))

    def test_stop_stops_all_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.yaml").write_text(
                "version: 1\nprofile: python-stdlib\napplication: todo-web\n",
                encoding="utf-8",
            )
            (root / "workspace" / "backend").mkdir(parents=True)
            (root / "workspace" / "frontend").mkdir()
            executor = FakeDockerExecutor()
            runner = DockerApplicationRunner(executor)
            started = runner.start(str(root))

            stopped = runner.stop(started.run_id)

            self.assertEqual(stopped.status, ApplicationRunStatus.STOPPED)
            self.assertEqual(
                len([command for command in executor.commands if command[:2] == ["docker", "stop"]]),
                2,
            )


if __name__ == "__main__":
    unittest.main()

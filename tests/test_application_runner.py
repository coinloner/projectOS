import tempfile
import unittest
from pathlib import Path

from app.sandbox.application_runner import (
    ApplicationRunStatus,
    DockerApplicationRunner,
)
from app.sandbox.docker_provider import CommandResult


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
    def test_start_uses_catalog_and_returns_fixed_service_urls(self) -> None:
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
            self.assertEqual(
                [service.host_url for service in result.services],
                ["http://127.0.0.1:8000", "http://127.0.0.1:8080"],
            )
            docker_runs = [command for command in executor.commands if "--detach" in command]
            self.assertEqual(len(docker_runs), 2)
            for command in docker_runs:
                self.assertIn("--read-only", command)
                self.assertIn("--cap-drop", command)
                self.assertIn("ALL", command)
                self.assertIn("--user", command)
                self.assertIn("65532:65532", command)
                self.assertNotIn("--privileged", command)

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

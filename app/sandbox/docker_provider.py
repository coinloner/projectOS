"""Docker SandboxProvider。Agent 从不直接接触此实现或 Docker Socket。"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time
from typing import Protocol

from app.sandbox.policy import SandboxSpec
from app.sandbox.result import SandboxResult, SandboxStatus


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class DockerExecutor(Protocol):
    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        ...


class SubprocessDockerExecutor:
    """唯一可调用 docker CLI 的低层适配器，不接受 Agent 文本命令。"""

    def run(self, command: list[str], timeout_seconds: int) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return CommandResult(
                exit_code=127,
                stderr="docker CLI 不可用",
            )
        except subprocess.TimeoutExpired as error:
            return CommandResult(
                exit_code=124,
                stdout=error.stdout or "",
                stderr=error.stderr or "",
                timed_out=True,
            )
        return CommandResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def ensure_image_available(
    executor: DockerExecutor, image: str, *, timeout_seconds: int = 10
) -> CommandResult:
    """确认受信任镜像存在；兼容 Docker Desktop 的 inspect 标签异常。"""
    inspected = executor.run(
        ["docker", "image", "inspect", image], timeout_seconds=timeout_seconds
    )
    if inspected.exit_code == 0:
        return inspected
    listed = executor.run(
        ["docker", "image", "ls", "--format={{.Repository}}:{{.Tag}}", image],
        timeout_seconds=timeout_seconds,
    )
    if listed.exit_code == 0 and image in listed.stdout.splitlines():
        return CommandResult(exit_code=0, stdout=listed.stdout)
    return listed if listed.exit_code != 0 else inspected


class DockerSandboxProvider:
    """以固定 Docker 安全参数运行已获批准的 SandboxSpec。"""

    def __init__(self, executor: DockerExecutor | None = None) -> None:
        self._executor = executor or SubprocessDockerExecutor()

    def run_check(self, spec: SandboxSpec) -> SandboxResult:
        started = time.monotonic()
        image_check = ensure_image_available(self._executor, spec.image)
        if image_check.exit_code != 0:
            return self._result(
                spec,
                SandboxStatus.SETUP_FAILED,
                image_check.exit_code,
                started,
                stderr=image_check.stderr,
                message="Docker daemon 不可用或受信任基础镜像尚未预置",
            )

        result = self._executor.run(
            self._run_command(spec),
            timeout_seconds=spec.limits.timeout_seconds,
        )
        if result.timed_out:
            status = SandboxStatus.TIMED_OUT
        elif result.exit_code == 0:
            status = SandboxStatus.PASSED
        else:
            status = SandboxStatus.FAILED
        return self._result(
            spec,
            status,
            result.exit_code,
            started,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run_command(self, spec: SandboxSpec) -> list[str]:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(spec.limits.pids),
            "--memory",
            spec.limits.memory,
            "--cpus",
            spec.limits.cpus,
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={spec.limits.tmpfs_size}",
            "--tmpfs",
            f"/site-packages:rw,exec,nosuid,nodev,size={spec.limits.tmpfs_size}",
            "--mount",
            f"type=bind,src={spec.workspace_path},dst=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
        ]
        if spec.dependencies_file and spec.wheel_cache:
            command.extend(
                [
                    "--mount",
                    f"type=bind,src={spec.dependencies_file},dst=/input/requirements.in,readonly",
                    "--mount",
                    f"type=bind,src={spec.wheel_cache},dst=/wheels,readonly",
                    "--env",
                    "PYTHONPATH=/site-packages",
                ]
            )
        command.append(spec.image)
        command.extend(self._fixed_command(spec))
        return command

    @staticmethod
    def _fixed_command(spec: SandboxSpec) -> list[str]:
        check = list(spec.profile.checks[spec.check_id].command)
        if not spec.dependencies_file or not spec.profile.checks[spec.check_id].install_dependencies:
            return check
        install = (
            "python -m pip install --no-index --find-links=/wheels "
            "--target /site-packages -r /input/requirements.in"
        )
        return ["sh", "-ec", install + " && exec " + " ".join(check)]

    @staticmethod
    def _result(
        spec: SandboxSpec,
        status: SandboxStatus,
        exit_code: int | None,
        started: float,
        *,
        stdout: str = "",
        stderr: str = "",
        message: str | None = None,
    ) -> SandboxResult:
        limit = spec.limits.output_char_limit
        return SandboxResult(
            status=status,
            check_id=spec.check_id,
            runtime_profile=spec.profile.id,
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=stdout[:limit],
            stderr=stderr[:limit],
            message=message,
        )

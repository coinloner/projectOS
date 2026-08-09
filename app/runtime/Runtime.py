import subprocess
from pathlib import Path
from typing import Optional


class Runtime:
    """命令执行层 —— 所有命令统一走这里，后续可在此层加入权限校验、日志等。"""

    @staticmethod
    def run(command: list, cwd: Optional[str] = None) -> dict:
        """
        执行任意命令。

        Args:
           command: 命令参数列表，例如：

             ["python", "--version"]
            cwd:     工作目录，不传则沿用当前进程的 cwd。

        Returns:
            {
                "stdout":     str,
                "stderr":     str,
                "returncode": int
            }
        """
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        return {
            "stdout":     result.stdout,
            "stderr":     result.stderr,
            "returncode": result.returncode,
        }

    @staticmethod
    def run_checked(
        command: list,
        cwd: str,
        allowed_commands: list,
    ) -> dict:
        """经过基础权限校验后执行命令。

        该方法用于未来 Workflow 的人工确认 shell 逃生舱：
        Workflow 负责询问用户是否允许，Runtime 负责统一校验和执行。
        """
        if not isinstance(command, list) or not command:
            raise RuntimeError("❌ command 必须是非空 list")

        if not all(isinstance(part, str) for part in command):
            raise RuntimeError("❌ command 中的每一项都必须是字符串")

        executable = Path(command[0]).name
        if executable not in allowed_commands and command[0] not in allowed_commands:
            raise PermissionError(
                f"❌ 命令 '{command[0]}' 不在白名单中"
            )

        cwd_path = Path(cwd).resolve()
        if not cwd_path.exists() or not cwd_path.is_dir():
            raise RuntimeError(f"❌ cwd 不存在或不是目录: {cwd_path}")

        return Runtime.run(command, cwd=str(cwd_path))

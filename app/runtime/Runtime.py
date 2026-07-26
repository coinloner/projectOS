import subprocess
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

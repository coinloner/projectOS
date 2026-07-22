import subprocess
from typing import Optional, Union


class Runtime:
    """命令执行层 —— 所有命令统一走这里，后续可在此层加入权限校验、日志等。"""

    @staticmethod
    def run(command: Union[str, list], cwd: Optional[str] = None) -> dict:
        """
        执行任意命令。

        Args:
            command: 要执行的命令，可以是字符串或列表。
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
            shell=True if isinstance(command, str) else False,
            capture_output=True,
            text=True,
        )

        return {
            "stdout":     result.stdout,
            "stderr":     result.stderr,
            "returncode": result.returncode,
        }

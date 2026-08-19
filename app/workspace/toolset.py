"""供代码、测试和审查节点使用的受限 workspace 工具。"""

from __future__ import annotations

from app.workspace.store import WorkspaceStore


class WorkspaceToolSet:
    """代码节点的最小文件能力。"""

    def __init__(self, project_path: str, *, read_char_limit: int | None = None) -> None:
        if read_char_limit is not None and read_char_limit < 200:
            raise ValueError("read_char_limit 至少为 200")
        self._store = WorkspaceStore(project_path)
        self._read_char_limit = read_char_limit

    def list_files(self) -> str:
        return self._store.list_files()

    def read_file(self, path: str) -> str:
        content = self._store.read_file(path)
        if self._read_char_limit is None or len(content) <= self._read_char_limit:
            return content
        head_length = self._read_char_limit // 2
        tail_length = self._read_char_limit - head_length
        omitted = len(content) - self._read_char_limit
        return (
            content[:head_length]
            + f"\n\n（中间已省略 {omitted} 个字符；此节点仅获授权读取摘要）\n\n"
            + content[-tail_length:]
        )

    def write_file(self, path: str, content: str) -> str:
        return self._store.write_file(path, content)


class TestToolSet(WorkspaceToolSet):
    """测试节点文件能力：可读 workspace，且只可写 tests/。"""

    def write_test_file(self, path: str, content: str) -> str:
        if not path.startswith("tests/"):
            raise PermissionError("测试文件只能写入 workspace/tests/ 目录")
        return self._store.write_file(path, content)


class CodeWorkspaceToolSet(WorkspaceToolSet):
    """代码节点文件能力：可写 workspace，但测试目录归测试节点。"""

    def write_file(self, path: str, content: str) -> str:
        if path.startswith("tests/"):
            raise PermissionError("tests/ 目录由测试节点写入，代码节点不能修改测试文件")
        return self._store.write_file(path, content)

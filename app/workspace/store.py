"""项目 workspace 的受限文件存储。"""

from __future__ import annotations

from pathlib import Path


class WorkspaceStore:
    """只允许在 ``<project>/workspace`` 下读写文本项目文件。"""

    _ALLOWED_SUFFIXES = frozenset(
        {
            ".css",
            ".html",
            ".js",
            ".json",
            ".md",
            ".py",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".yaml",
            ".yml",
        }
    )
    _IGNORED_DIRECTORIES = frozenset(
        {".git", ".venv", "__pycache__", "node_modules"}
    )
    _ALLOWED_FILENAMES = frozenset({"Dockerfile", "Makefile", "Procfile", "justfile"})

    def __init__(self, project_path: str) -> None:
        self._root = (Path(project_path) / "workspace").resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """受限 workspace 的根路径，仅供固定运行器使用。"""
        return self._root

    def list_files(self) -> str:
        """列出 workspace 内允许被 Agent 看见的文件。"""
        files = [
            path.relative_to(self._root).as_posix()
            for path in self._root.rglob("*")
            if path.is_file()
            and not any(part in self._IGNORED_DIRECTORIES for part in path.parts)
            and path.suffix.lower() in self._ALLOWED_SUFFIXES
        ]
        return "\n".join(sorted(files)) or "（workspace 暂无项目文件）"

    def implementation_file_count(self) -> int:
        """返回非测试、非说明文件的数量，作为代码交付的最低存在性证据。"""
        return sum(
            1
            for path in self._root.rglob("*")
            if path.is_file()
            and not any(part in self._IGNORED_DIRECTORIES for part in path.parts)
            and "tests" not in path.relative_to(self._root).parts
            and path.suffix.lower() in self._ALLOWED_SUFFIXES
            and path.suffix.lower() != ".md"
        )

    def read_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            return f"（文件不存在: {path}）"
        if not target.is_file():
            raise IsADirectoryError(f"'{path}' 不是文件")
        return target.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 workspace/{target.relative_to(self._root).as_posix()}"

    def _resolve(self, path: str) -> Path:
        if not path or not path.strip():
            raise ValueError("workspace 路径不能为空")
        candidate = Path(path)
        if candidate.is_absolute():
            raise PermissionError("workspace 工具只接受相对路径")
        if candidate.name not in self._ALLOWED_FILENAMES and candidate.suffix.lower() not in self._ALLOWED_SUFFIXES:
            allowed = ", ".join(sorted(self._ALLOWED_SUFFIXES))
            raise PermissionError(f"不允许操作该文件类型，可用后缀: {allowed}")

        target = (self._root / candidate).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise PermissionError("workspace 路径不能越出项目目录") from error
        if any(part in self._IGNORED_DIRECTORIES for part in target.relative_to(self._root).parts):
            raise PermissionError("不允许操作受保护目录")
        return target

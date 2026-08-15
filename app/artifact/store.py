"""项目 Markdown 产物的受限文件存储。"""

from __future__ import annotations

from pathlib import Path


class ArtifactStore:
    """管理 ProjectOS 已知产物，不提供任意路径读写。"""

    _FILENAMES = {
        "requirement": "requirement.md",
        "architecture": "architecture.md",
        "tasks": "tasks.md",
        "implementation": "implementation.md",
        "environment": "environment.md",
        "tests": "tests.md",
        "review": "review.md",
    }

    def __init__(self, project_path: str) -> None:
        self._project_path = Path(project_path)

    @property
    def project_path(self) -> str:
        """关联项目目录，供其他受限存储构造同一项目边界。"""
        return str(self._project_path)

    def save(self, artifact: str, content: str) -> None:
        path = self._path_for(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def load(self, artifact: str) -> str:
        path = self._path_for(artifact)
        if not path.exists():
            return f"（尚未创建 {self._FILENAMES[artifact]}）"
        return path.read_text(encoding="utf-8")

    def exists(self, artifact: str) -> bool:
        """返回已知产物是否已落盘，不读取内容。"""
        return self._path_for(artifact).exists()

    @classmethod
    def filename_for(cls, artifact: str) -> str:
        try:
            return cls._FILENAMES[artifact]
        except KeyError as error:
            available = ", ".join(sorted(cls._FILENAMES))
            raise ValueError(
                f"未知产物 '{artifact}'，可用产物: {available}"
            ) from error

    def _path_for(self, artifact: str) -> Path:
        return self._project_path / self.filename_for(artifact)

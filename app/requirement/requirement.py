from dataclasses import dataclass
from pathlib import Path


@dataclass
class RequirementDocument:
    content: str

    @classmethod
    def empty(cls) -> "RequirementDocument":
        return cls(content="")


class Requirement:
    """需求文档管理 —— 对项目目录下的 requirement.md 进行读写。"""



    @staticmethod
    def _file_path(project_path: str) -> Path:
        return Path(project_path) / "requirement.md"

    @staticmethod
    def save(project_path: str, document: RequirementDocument) -> None:
        """在项目目录下创建 requirement.md 并写入内容。"""
        file_path = Requirement._file_path(project_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(document.content, encoding="utf-8")

    @staticmethod
    def load(project_path: str) -> RequirementDocument:
        """读取项目目录下的 requirement.md，返回 RequirementDocument。"""
        file_path = Requirement._file_path(project_path)

        if not file_path.exists():
            raise FileNotFoundError(f"❌ 找不到需求文档: {file_path}")

        return RequirementDocument(content=file_path.read_text(encoding="utf-8"))

    @staticmethod
    def update(project_path: str, document: RequirementDocument) -> None:
        """覆盖写入 requirement.md，本质复用 save()。"""
        Requirement.save(project_path, document)




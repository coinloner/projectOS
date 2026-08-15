"""需求领域的本地文档能力。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RequirementDocument:
    """需求文档的纯数据表示。"""

    content: str

    @classmethod
    def empty(cls) -> "RequirementDocument":
        return cls(content="")


class RequirementService:
    """管理单个项目的 requirement.md，不依赖 Agent 或工具运行时。"""

    def __init__(self, project_path: str) -> None:
        self._path = Path(project_path) / "requirement.md"

    def save(self, document: RequirementDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(document.content, encoding="utf-8")

    def load(self) -> RequirementDocument:
        if not self._path.exists():
            raise FileNotFoundError(f"找不到需求文档: {self._path}")
        return RequirementDocument(content=self._path.read_text(encoding="utf-8"))

    def update(self, document: RequirementDocument) -> None:
        self.save(document)

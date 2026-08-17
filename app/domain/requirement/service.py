"""需求领域的本地文档能力。"""

from app.artifact.toolset import ArtifactToolSet


class RequirementService:
    """封装需求节点的 requirement.md 读写能力。"""

    def __init__(self, project_path: str) -> None:
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="requirement",
            readable_artifacts=("requirement",),
        )

    def save_requirement(self, content: str) -> str:
        return self._artifacts.save(content)

    def load_requirement(self) -> str:
        return self._artifacts.load("requirement")

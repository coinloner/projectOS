"""需求领域的本地文档能力。"""

from app.artifact.toolset import ArtifactToolSet


class RequirementService:
    """封装需求节点的 requirement.md 读写能力。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="requirement",
            readable_artifacts=("requirement",),
        )

    def save_requirement(self, content: str) -> str:
        result = self._artifacts.save(content)
        # 建立项目级 AC 追踪矩阵；后续 Architecture/Code/Test/Runtime 证据
        # 通过稳定的 requirement_id 绑定，避免只依赖 Markdown 追溯。
        from app.orchestration.delivery import DeliveryStore

        DeliveryStore(self._project_path).initialize_from_markdown(content)
        return result

    def load_requirement(self) -> str:
        return self._artifacts.load("requirement")

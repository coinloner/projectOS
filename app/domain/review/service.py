"""交付审查领域的本地证据能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.runtime.state import runtime_snapshot
from app.policy.quality import ProjectQualityPolicy
from app.workspace.toolset import WorkspaceToolSet


class ReviewService:
    """封装审查节点的只读摘要与 review.md 写入边界。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="review",
            readable_artifacts=("requirement", "architecture", "tasks", "environment", "implementation", "tests"),
            read_char_limit=3_000,
        )
        self._workspace = WorkspaceToolSet(project_path, read_char_limit=4_000)

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_review(self, content: str) -> str:
        # 将确定性结构审查固化到 review.md，避免质量结论只存在于模型上下文。
        from pathlib import Path

        root = Path(self._project_path)
        if (root / "runtime.yaml").is_file():
            content += "\n\n## 确定性质量策略\n\n" + ProjectQualityPolicy().render(str(root))
        return self._artifacts.save(content)

    def list_workspace_files(self) -> str:
        return self._workspace.list_files()

    def read_workspace_file(self, path: str) -> str:
        return self._workspace.read_file(path)

    def inspect_runtime(self) -> str:
        return runtime_snapshot(self._project_path).as_text()

    def inspect_quality(self) -> str:
        return ProjectQualityPolicy().render(self._project_path)

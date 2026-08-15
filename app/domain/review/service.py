"""交付审查领域的本地证据能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.runtime.state import runtime_snapshot
from app.workspace.toolset import WorkspaceToolSet


class ReviewService:
    """封装审查节点的只读摘要与 review.md 写入边界。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="review",
            readable_artifacts=("requirement", "architecture", "tasks", "implementation", "tests"),
            read_char_limit=3_000,
        )
        self._workspace = WorkspaceToolSet(project_path, read_char_limit=4_000)

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_review(self, content: str) -> str:
        return self._artifacts.save(content)

    def list_workspace_files(self) -> str:
        return self._workspace.list_files()

    def read_workspace_file(self, path: str) -> str:
        return self._workspace.read_file(path)

    def inspect_runtime(self) -> str:
        return runtime_snapshot(self._project_path).as_text()

"""代码交付领域的本地能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.runtime.state import runtime_snapshot
from app.workspace.toolset import WorkspaceToolSet


class CodeService:
    """封装代码节点的产物读取与受限 workspace 写入能力。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="implementation",
            readable_artifacts=("requirement", "architecture", "tasks"),
        )
        self._workspace = WorkspaceToolSet(project_path)

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_implementation(self, content: str) -> str:
        return self._artifacts.save(content)

    def list_workspace_files(self) -> str:
        return self._workspace.list_files()

    def read_workspace_file(self, path: str) -> str:
        return self._workspace.read_file(path)

    def write_workspace_file(self, path: str, content: str) -> str:
        return self._workspace.write_file(path, content)

    def inspect_runtime(self) -> str:
        return runtime_snapshot(self._project_path).as_text()

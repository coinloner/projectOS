"""任务分解领域的本地产物能力。"""

from app.artifact.toolset import ArtifactToolSet


class TaskService:
    """封装任务节点固定的产物读写边界。"""

    def __init__(self, project_path: str) -> None:
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="tasks",
            readable_artifacts=("requirement", "architecture"),
        )

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_tasks(self, content: str) -> str:
        return self._artifacts.save(content)

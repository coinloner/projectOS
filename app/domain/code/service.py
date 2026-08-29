"""代码交付领域的本地能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.execution_context import ExecutionContext, ExecutionMode
from app.domain.code.git_service import GitCodeIntegrationService, GitCodeStagingService
from app.runtime.state import runtime_snapshot
from app.workspace.toolset import CodeWorkspaceToolSet


class CodeService:
    """封装代码节点的产物读取与受限 workspace 写入能力。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="implementation",
            # 修复节点需要保留既有实现摘要中的历史凭证；这只增加只读权限，
            # 不改变 workspace 写入边界，也不会让分区节点看到该工具。
            readable_artifacts=(
                "requirement", "architecture", "architecture_contract", "tasks",
                "environment", "implementation",
            ),
        )
        self._workspace = CodeWorkspaceToolSet(project_path, read_char_limit=4_000)

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


class CodeStagingService:
    """CodeAgent 分区执行使用的输入读取和 Git worktree 写入能力。"""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._service = GitCodeStagingService(project_path)

    def load_input(self, context: ExecutionContext, ref_id: str) -> str:
        return self._service.load_input(context, ref_id)

    def write_staged_file(
        self, context: ExecutionContext, path: str, content: str
    ) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有代码分区节点可以写入 Git task worktree")
        return self._service.write_staged_file(context, path, content)

    def load_change_set(self, trace_id: str, work_item_id: str):
        return self._service.load_change_set(trace_id, work_item_id)

    def load_baseline(self, trace_id: str) -> str:
        return self._service.load_baseline(trace_id)

    def refresh_baseline(self, trace_id: str, *, integrated_commits: tuple[str, ...] = ()) -> str:
        return self._service.refresh_baseline(trace_id, integrated_commits=integrated_commits)


class CodeIntegrationService:
    """代码集成的策略检查与一次性合并。"""

    def __init__(self, project_path: str) -> None:
        self._service = GitCodeIntegrationService(project_path)

    def review_evidence(self, context: ExecutionContext) -> str:
        return self._service.review_evidence(context)

    def integrate(self, context: ExecutionContext, *, review: object | None = None) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("代码合并只能由 INTEGRATION 节点执行")
        return self._service.integrate(context, review=review)

    def integrate_wave(self, context: ExecutionContext) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("Wave 合并只能由 INTEGRATION 控制面执行")
        return self._service.integrate(
            context, publish_summary=False, refresh_baseline=True
        )

    def record_review(self, context: ExecutionContext, review: object) -> None:
        self._service.record_review(context, review)

    @staticmethod
    def _validate_local_imports(integration):
        return GitCodeIntegrationService._validate_local_imports(integration)

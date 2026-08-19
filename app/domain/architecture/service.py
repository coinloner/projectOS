"""架构领域的本地产物能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.artifact.repository import ArtifactRepository
from app.execution_context import ExecutionContext, ExecutionMode


class ArchitectureService:
    """封装架构节点固定的产物读写边界。"""

    def __init__(self, project_path: str) -> None:
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="architecture",
            readable_artifacts=("requirement",),
        )

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_architecture(self, content: str) -> str:
        return self._artifacts.save(content)


class ArchitectureArtifactWorkflow:
    """架构分区、集成和发布流程的受信工具实现。"""

    def __init__(self, project_path: str) -> None:
        self._repository = ArtifactRepository(project_path)

    def load_input(self, context: ExecutionContext, ref_id: str) -> str:
        ref = next((candidate for candidate in context.input_refs if candidate.ref_id == ref_id), None)
        if ref is None:
            raise PermissionError("当前工作项无权读取该产物引用")
        return self._repository.load_ref(ref)

    def write_staged(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有分区执行节点可以写入暂存产物")
        self._validate_size(content, _STAGED_CHAR_LIMITS.get(context.output_slot or "", 4500))
        staged = self._repository.write_staged(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="architecture",
            slot=context.output_slot or "",
            content=content,
        )
        return f"已写入架构暂存输出: {staged.ref.ref_id}"

    def create_candidate(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("只有集成节点可以创建候选版本")
        if context.publish_target != "architecture":
            raise PermissionError("当前集成节点未获 architecture 候选创建授权")
        self._validate_size(content, 9000)
        candidate = self._repository.create_candidate(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="architecture",
            content=content,
            source_refs=context.input_refs,
        )
        return (
            f"已创建架构候选: {candidate.id}；"
            f"集成状态: {candidate.report.status}"
        )

    @staticmethod
    def _validate_size(content: str, limit: int) -> None:
        if len(content) > limit:
            raise ValueError(
                f"当前架构产物超过 {limit} 字符上限；请压缩为决策、接口和未决项。"
            )


_STAGED_CHAR_LIMITS = {
    "baseline": 3200,
    "api": 4200,
    "data": 4200,
    "frontend": 4200,
    "design": 6000,
}

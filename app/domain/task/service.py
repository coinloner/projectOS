"""任务产物的受控分区、集成和候选发布能力。"""

from app.artifact.repository import ArtifactRepository
from app.execution_context import ExecutionContext, ExecutionMode


class TaskArtifactWorkflow:
    """TaskAgent 的标准执行工作流。

    TaskAgent 不再直接读写项目根目录。所有输入都必须来自 GraphRunner
    发放的 ``ArtifactRef``，输出先进入当前 WorkItem 的暂存位置，再由集成
    WorkItem 创建候选，最终由确定性的质量门发布。
    """

    def __init__(self, project_path: str) -> None:
        self._repository = ArtifactRepository(project_path)

    def load_input(self, context: ExecutionContext, ref_id: str) -> str:
        ref = next(
            (candidate for candidate in context.input_refs if candidate.ref_id == ref_id),
            None,
        )
        if ref is None:
            raise PermissionError("当前工作项无权读取该产物引用")
        return self._repository.load_ref(ref)

    def write_staged(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.PARTITIONED:
            raise PermissionError("只有任务分区节点可以写入暂存产物")
        self._validate_size(content, 7_000)
        staged = self._repository.write_staged(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="tasks",
            slot=context.slot or "",
            content=content,
        )
        return f"已写入任务暂存输出: {staged.ref.ref_id}"

    def create_candidate(self, context: ExecutionContext, content: str) -> str:
        if context.execution_mode is not ExecutionMode.INTEGRATION:
            raise PermissionError("只有任务集成节点可以创建候选版本")
        if context.publish_target != "tasks":
            raise PermissionError("当前集成节点未获 tasks 候选创建授权")
        self._validate_size(content, 9_000)
        candidate = self._repository.create_candidate(
            trace_id=context.trace_id,
            work_item_id=context.work_item_id,
            artifact_key="tasks",
            content=content,
            source_refs=context.input_refs,
        )
        return f"已创建任务候选: {candidate.id}；集成状态: {candidate.report.status}"

    @staticmethod
    def _validate_size(content: str, limit: int) -> None:
        if not content.strip():
            raise ValueError("任务产物内容不能为空")
        if len(content) > limit:
            raise ValueError(f"当前任务产物超过 {limit} 字符上限；请压缩为 MVP 必要内容。")

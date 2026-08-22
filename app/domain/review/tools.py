"""ReviewAgent 的工具合同。"""

from app.domain.review.service import ReviewService
from app.execution_context import ExecutionContext
from app.orchestration.trace import TraceStore
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef, ToolSetSource


class ReviewToolSet:
    """将 ReviewService 适配为审查 Agent 的本地工具。"""

    def __init__(self, service: ReviewService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_review(self, content: str) -> str:
        return self._service.save_review(content)

    def list_workspace_files(self) -> str:
        return self._service.list_workspace_files()

    def read_workspace_file(self, path: str) -> str:
        return self._service.read_workspace_file(path)

    def inspect_runtime(self) -> str:
        return self._service.inspect_runtime()

    def inspect_quality(self) -> str:
        return self._service.inspect_quality()


class SandboxEvidenceReaderToolSet:
    """Review 读取当前 Trace 的受控 Docker 证据，不提供修改入口。"""

    def __init__(self, traces: TraceStore) -> None:
        self._traces = traces

    def list_sandbox_evidence(self, context: ExecutionContext) -> str:
        evidence = self._traces.list_sandbox_evidence(context)
        if not evidence:
            return "当前 Trace 尚无 sandbox evidence"
        return "\n".join(
            f"evidence_id={item.id} status={item.status.value} "
            f"check_id={item.check_id} exit_code={item.exit_code} "
            f"duration_ms={item.duration_ms}"
            for item in evidence
        )

    def load_sandbox_evidence(
        self, context: ExecutionContext, evidence_id: str
    ) -> str:
        return self._traces.load_sandbox_evidence(
            context, evidence_id
        ).as_agent_text()


def register_review_tools(
    gateway: ToolGateway,
    project_path: str,
    *,
    traces: TraceStore | None = None,
) -> None:
    """审查节点只读阶段摘要和 workspace 摘要，只能写 review.md。"""
    tools = ReviewToolSet(ReviewService(project_path))
    evidence_tools = SandboxEvidenceReaderToolSet(traces or TraceStore(project_path))
    gateway.register_toolset(
        domain="review",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物摘要。可读取: requirement、architecture、tasks、environment、implementation、tests。",
                        parameters={
                            "type": "object",
                            "properties": {"artifact": {"type": "string", "description": "前置产物标识"}},
                            "required": ["artifact"],
                        },
                    ),
                    tools.load_artifact,
                ),
                (
                    ToolDef(
                        name="save_review",
                        description="保存完整交付审查报告到 review.md。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                        },
                    ),
                    tools.save_review,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="review",
        name="sandbox_evidence",
        toolset=ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="list_sandbox_evidence",
                        description="列出当前 Trace 中由 Docker 记录的测试证据摘要。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    evidence_tools.list_sandbox_evidence,
                ),
                (
                    ToolDef(
                        name="load_sandbox_evidence",
                        description="读取当前 Trace 中指定 Docker 测试证据的完整受控输出。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "evidence_id": {
                                    "type": "string",
                                    "description": "list_sandbox_evidence 返回的证据标识",
                                }
                            },
                            "required": ["evidence_id"],
                        },
                    ),
                    evidence_tools.load_sandbox_evidence,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="review",
        name="workspace_summary",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="list_workspace_files",
                        description="列出 workspace 中允许访问的项目文件。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.list_workspace_files,
                ),
                (
                    ToolDef(
                        name="read_workspace_file",
                        description="读取 workspace 内单个文本文件的受限摘要。",
                        parameters={
                            "type": "object",
                            "properties": {"path": {"type": "string", "description": "workspace 相对路径"}},
                            "required": ["path"],
                        },
                    ),
                    tools.read_workspace_file,
                ),
                (
                    ToolDef(
                        name="inspect_runtime",
                        description="读取当前 runtime、依赖缓存和 sandbox 执行状态摘要。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.inspect_runtime,
                ),
                (
                    ToolDef(
                        name="inspect_quality",
                        description="执行确定性的项目分层、入口规模、测试和启动脚本质量检查。",
                        parameters={"type": "object", "properties": {}},
                    ),
                    tools.inspect_quality,
                ),
            ]
        ),
    )

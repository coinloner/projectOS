"""TestAgent 的工具合同。"""

from app.domain.test.service import TestService
from app.execution_context import ExecutionContext
from app.orchestration.trace import TraceStore
from app.orchestration.evidence import RuntimeEvidence
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ExecutionToolSetSource, ToolDef, ToolSetSource


class TestToolSet:
    """将 TestService 适配为测试 Agent 的本地工具。"""

    def __init__(self, service: TestService) -> None:
        self._service = service

    def load_artifact(self, artifact: str) -> str:
        return self._service.load_artifact(artifact)

    def save_tests(self, content: str) -> str:
        return self._service.save_tests(content)

    def list_workspace_files(self) -> str:
        return self._service.list_workspace_files()

    def read_workspace_file(self, path: str) -> str:
        return self._service.read_workspace_file(path)

    def write_test_file(self, path: str, content: str) -> str:
        return self._service.write_test_file(path, content)


class SandboxEvidenceToolSet:
    """把固定 Docker check 绑定到当前 WorkItem 的 Trace 证据。"""

    def __init__(self, service: TestService, traces: TraceStore) -> None:
        self._service = service
        self._traces = traces

    def run_sandbox_check(
        self, context: ExecutionContext, check_id: str | None = None
    ) -> str:
        check_id = check_id or "unit"
        result = self._service.run_sandbox_check(check_id)
        evidence = self._traces.record_sandbox_evidence(context, result)
        self._traces.record_runtime_evidence(RuntimeEvidence.create(
            trace_id=context.trace_id,
            phase="behavior_test" if check_id in {"unit", "web-unit"} else check_id,
            status=evidence.status.value,
            work_item_id=context.work_item_id,
            exit_code=evidence.exit_code,
            duration_ms=evidence.duration_ms,
            stdout=evidence.stdout,
            stderr=evidence.stderr,
            message=evidence.message,
        ))
        return evidence.as_agent_text()


def register_test_tools(
    gateway: ToolGateway,
    project_path: str,
    *,
    traces: TraceStore | None = None,
) -> None:
    """测试节点可读实现、仅能写 tests/，并只能运行受信固定检查。"""
    service = TestService(project_path)
    tools = TestToolSet(service)
    evidence_tools = SandboxEvidenceToolSet(
        service, traces or TraceStore(project_path)
    )
    gateway.register_toolset(
        domain="test",
        name="project_artifacts",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="load_artifact",
                        description="读取前置产物。可读取: requirement、tasks、environment、implementation。",
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
                        name="save_tests",
                        description="保存完整测试报告到 tests.md。",
                        parameters={
                            "type": "object",
                            "properties": {"content": {"type": "string", "description": "完整 Markdown 内容"}},
                            "required": ["content"],
                        },
                        completion_policy="final",
                    ),
                    tools.save_tests,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="test",
        name="workspace",
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
                        description="读取 workspace 内允许访问的文本文件。",
                        parameters={
                            "type": "object",
                            "properties": {"path": {"type": "string", "description": "workspace 相对路径"}},
                            "required": ["path"],
                        },
                    ),
                    tools.read_workspace_file,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="test",
        name="test_writer",
        toolset=ToolSetSource(
            [
                (
                    ToolDef(
                        name="write_test_file",
                        description="仅在 workspace/tests/ 下写入 Python unittest 测试文件。",
                        parameters={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "tests/ 下的相对路径"},
                                "content": {"type": "string", "description": "完整测试文件内容"},
                            },
                            "required": ["path", "content"],
                        },
                    ),
                    tools.write_test_file,
                ),
            ]
        ),
    )
    gateway.register_toolset(
        domain="test",
        name="test_runner",
        toolset=ExecutionToolSetSource(
            [
                (
                    ToolDef(
                        name="run_sandbox_check",
                        description=(
                            "在受控 Docker sandbox 中执行固定检查。check_id 只能选 profile "
                            "白名单：unit（Python unittest 测试）、web-unit（Node 前端测试）或 "
                            "runtime-smoke（合同声明的后端入口组装探针）。"
                            "镜像缺失时记录 setup_failed，不请求外部能力。"
                        ),
                        parameters={
                            "type": "object",
                            "properties": {
                                "check_id": {
                                    "type": "string",
                                    "description": "白名单检查标识：unit、web-unit 或 runtime-smoke，默认 unit",
                                }
                            },
                        },
                    ),
                    evidence_tools.run_sandbox_check,
                ),
            ]
        ),
    )

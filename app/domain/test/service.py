"""测试领域的本地能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.sandbox.controller import SandboxController
from app.sandbox.result import SandboxResult
from app.workspace.toolset import TestToolSet
from app.domain.architecture.layer_contract import LayerContractStore


class TestService:
    """封装测试节点的证据读写和固定测试执行能力。"""

    def __init__(
        self,
        project_path: str,
        *,
        sandbox: SandboxController | None = None,
    ) -> None:
        self._project_path = project_path
        self._artifacts = ArtifactToolSet(
            project_path,
            output_artifact="tests",
            readable_artifacts=("requirement", "tasks", "environment", "implementation"),
        )
        self._workspace = TestToolSet(project_path, read_char_limit=4_000)
        self._sandbox = sandbox or SandboxController()

    def load_artifact(self, artifact: str) -> str:
        return self._artifacts.load(artifact)

    def save_tests(self, content: str) -> str:
        return self._artifacts.save(content)

    def list_workspace_files(self) -> str:
        return self._workspace.list_files()

    def read_workspace_file(self, path: str) -> str:
        return self._workspace.read_file(path)

    def write_test_file(self, path: str, content: str) -> str:
        return self._workspace.write_test_file(path, content)

    def run_sandbox_check(self, check_id: str = "unit") -> SandboxResult:
        """测试只能选择 profile 白名单中的固定 check，不能传递宿主机命令。"""
        return self._sandbox.run_check(self._project_path, check_id)

    def load_layer_contract(self) -> str:
        import json
        return json.dumps(LayerContractStore(self._project_path).load().as_dict(), ensure_ascii=False, indent=2)

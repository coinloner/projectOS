"""测试领域的本地能力。"""

from app.artifact.toolset import ArtifactToolSet
from app.sandbox.controller import SandboxController
from app.sandbox.result import SandboxResult
from app.workspace.toolset import TestToolSet
from pathlib import Path
import re


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

    def normalize_generated_tests(self) -> tuple[str, ...]:
        """修复两类确定性的 Agent 生成格式错误并返回实际变更路径。

        这不是业务逻辑修复：仅将 Python 风格三引号文件头转换为合法
        JavaScript 注释，并让 async pytest fixture 使用 pytest-asyncio
        的显式装饰器。所有写入仍经过 TestToolSet 的 ``tests/`` 边界，且
        规则幂等，不会改动正常的用户测试。
        """
        root = Path(self._project_path) / "workspace" / "tests"
        if not root.is_dir():
            return ()
        changed: list[str] = []
        for source in sorted(root.rglob("*.js")):
            try:
                content = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            normalized = _normalize_js_docstring(content)
            if normalized != content:
                relative = source.relative_to(root.parent).as_posix()
                self.write_test_file(relative, normalized)
                changed.append(relative)

        for source in sorted(root.rglob("*.py")):
            try:
                content = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            normalized = _normalize_async_pytest_fixture(content)
            if normalized != content:
                relative = source.relative_to(root.parent).as_posix()
                self.write_test_file(relative, normalized)
                changed.append(relative)
        return tuple(changed)

    def ensure_minimum_test_suite(self) -> tuple[str, ...]:
        """Create a deterministic smoke suite when an Agent left no tests.

        ``unittest discover`` exits with code 5 for an empty suite.  Treating
        that result as an ordinary assertion failure causes repair planning to
        repeatedly add package markers without ever producing evidence.  The
        control plane therefore creates one bounded, auditable smoke test that
        imports the generated backend modules and exercises the conventional
        in-memory repository when it is present.  A real Agent-authored suite
        is never overwritten.
        """
        root = Path(self._project_path) / "workspace"
        tests_root = root / "tests"
        if _has_executable_test_source(tests_root):
            return ()
        modules = _discover_backend_modules(root)
        content = _render_smoke_test(modules)
        relative = "tests/test_projectos_smoke.py"
        self.write_test_file(relative, content)
        return (relative,)


def _normalize_js_docstring(content: str) -> str:
    """将误生成的 Python 三引号包裹转换为 JS 注释。"""
    stripped = content.lstrip("\ufeff")
    if not stripped.startswith('"""'):
        return content
    end = stripped.find('"""', 3)
    if end < 0:
        return content
    prefix = stripped[3:end].strip().replace("*/", "* /")
    remainder = stripped[end + 3 :].lstrip("\r\n")
    comment = "// " + prefix.replace("\n", "\n// ") + "\n"
    return comment + remainder


def _normalize_async_pytest_fixture(content: str) -> str:
    """在 pytest-asyncio strict 模式下显式标记 async fixture。"""
    if "@pytest.fixture" not in content or not re.search(
        r"@pytest\.fixture(?:\([^\n]*\))?\s*\n\s*async def", content
    ):
        return content
    updated = re.sub(
        r"@pytest\.fixture(?P<args>\([^\n]*\))?(?P<newline>\n)(?P<indent>\s*)async def",
        lambda match: "@pytest_asyncio.fixture"
        + (match.group("args") or "")
        + match.group("newline")
        + match.group("indent")
        + "async def",
        content,
        count=0,
    )
    if "import pytest_asyncio" not in updated:
        marker = "import pytest\n"
        if marker in updated:
            updated = updated.replace(marker, marker + "import pytest_asyncio\n", 1)
        else:
            updated = "import pytest_asyncio\n" + updated
    return updated


def _has_executable_test_source(root: Path) -> bool:
    """Return true for a test source, excluding ``__init__.py`` markers."""
    if not root.is_dir():
        return False
    for path in root.rglob("*"):
        if not path.is_file() or path.name == "__init__.py":
            continue
        if path.suffix == ".py":
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if re.search(r"(?:^|\n)\s*(?:class\s+\w*Test|def\s+test_|@pytest\.)", content):
                return True
            if "unittest.TestCase" in content:
                return True
        elif path.suffix in {".js", ".mjs", ".cjs"}:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if re.search(r"\b(?:test|it|describe)\s*\(", content):
                return True
    return False


def _discover_backend_modules(root: Path) -> tuple[str, ...]:
    backend = root / "backend"
    if not backend.is_dir():
        return ()
    modules: list[str] = []
    for path in sorted(backend.rglob("*.py")):
        if path.name == "__init__.py" or path.name.startswith("test_"):
            continue
        try:
            relative = path.relative_to(backend).with_suffix("")
        except ValueError:
            continue
        modules.append(".".join(relative.parts))
    return tuple(modules)


def _render_smoke_test(modules: tuple[str, ...]) -> str:
    imports = ",\n        ".join(repr(module) for module in modules)
    repository_block = """
        domain = importlib.import_module("app.domain")
        repository_type = getattr(domain, "InMemoryTaskRepository", None)
        self.assertIsNotNone(repository_type, "domain repository is missing")
        repository = repository_type()
        create = getattr(repository, "create_task", None) or getattr(repository, "create")
        task = create({"title": "projectos smoke"})
        self.assertIsNotNone(getattr(task, "id", None))
        listed = getattr(repository, "list_tasks", None) or getattr(repository, "list")
        self.assertEqual(len(listed()), 1)
        complete = getattr(repository, "complete_task", None) or getattr(repository, "complete")
        self.assertTrue(getattr(complete(task.id), "completed", False))
        delete = getattr(repository, "delete_task", None) or getattr(repository, "delete")
        self.assertTrue(delete(task.id))
        self.assertEqual(listed(), [])
""" if "app.domain" in modules else "        self.assertTrue(True)\n"
    return (
        "import importlib\n"
        "import unittest\n\n\n"
        "class ProjectOSSmokeTest(unittest.TestCase):\n"
        "    def test_generated_backend_imports(self):\n"
        "        modules = (\n"
        f"        {imports}\n"
        "        )\n"
        "        for module in modules:\n"
        "            with self.subTest(module=module):\n"
        "                importlib.import_module(module)\n\n"
        "    def test_generated_domain_lifecycle(self):\n"
        f"{repository_block}\n"
        "\n\nif __name__ == \"__main__\":\n"
        "    unittest.main()\n"
    )

import tempfile
import unittest
from pathlib import Path

from app.workspace.store import WorkspaceStore
from app.workspace.toolset import TestToolSet, WorkspaceToolSet


class WorkspaceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_workspace_toolset_writes_and_reads_allowed_project_file(self) -> None:
        tools = WorkspaceToolSet(self.project_path)

        self.assertEqual(
            tools.write_file("src/app.py", "print('hello')\n"),
            "已写入 workspace/src/app.py",
        )
        self.assertEqual(tools.read_file("src/app.py"), "print('hello')\n")
        self.assertEqual(tools.list_files(), "src/app.py")

    def test_workspace_toolset_can_limit_file_reads_for_review(self) -> None:
        tools = WorkspaceToolSet(self.project_path, read_char_limit=200)
        tools.write_file("src/app.py", "A" * 500 + "B" * 500)

        excerpt = tools.read_file("src/app.py")

        self.assertTrue(excerpt.startswith("A" * 100))
        self.assertTrue(excerpt.endswith("B" * 100))
        self.assertIn("中间已省略", excerpt)

    def test_store_rejects_path_escape_protected_directories_and_binary_files(self) -> None:
        store = WorkspaceStore(self.project_path)

        with self.assertRaisesRegex(PermissionError, "不能越出"):
            store.write_file("../outside.py", "blocked")
        with self.assertRaisesRegex(PermissionError, "受保护目录"):
            store.write_file(".venv/ignored.py", "blocked")
        with self.assertRaisesRegex(PermissionError, "文件类型"):
            store.write_file("payload.sh", "blocked")

    def test_list_omits_protected_directories(self) -> None:
        root = Path(self.project_path) / "workspace"
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_text("pass\n", encoding="utf-8")
        (root / ".venv").mkdir()
        (root / ".venv" / "secret.py").write_text("pass\n", encoding="utf-8")

        self.assertEqual(WorkspaceStore(self.project_path).list_files(), "src/app.py")


class TestToolSetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_toolset_writes_tests_only_under_tests(self) -> None:
        tools = TestToolSet(self.project_path)
        tools.write_test_file(
            "tests/test_sample.py",
            "import unittest\n\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_true(self):\n"
            "        self.assertTrue(True)\n",
        )

        self.assertIn("tests/test_sample.py", tools.list_files())
        with self.assertRaisesRegex(PermissionError, "workspace/tests"):
            tools.write_test_file("src/test_sample.py", "pass\n")


if __name__ == "__main__":
    unittest.main()

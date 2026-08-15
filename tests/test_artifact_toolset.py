import tempfile
import unittest
from pathlib import Path

from app.artifact.store import ArtifactStore
from app.artifact.toolset import ArtifactToolSet


class ArtifactToolSetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_toolset_writes_its_fixed_output_artifact(self) -> None:
        tools = ArtifactToolSet(
            self.project_path,
            output_artifact="architecture",
            readable_artifacts=("requirement",),
        )

        self.assertEqual(tools.save("# Architecture"), "已保存 architecture.md")
        self.assertEqual(
            (Path(self.project_path) / "architecture.md").read_text(encoding="utf-8"),
            "# Architecture",
        )

    def test_toolset_reads_only_explicitly_allowed_artifacts(self) -> None:
        store = ArtifactStore(self.project_path)
        store.save("requirement", "# Requirement")
        store.save("tasks", "# Tasks")
        tools = ArtifactToolSet(
            self.project_path,
            output_artifact="architecture",
            readable_artifacts=("requirement",),
        )

        self.assertEqual(tools.load("requirement"), "# Requirement")
        with self.assertRaisesRegex(PermissionError, "无权读取"):
            tools.load("tasks")

    def test_unknown_artifact_fails_during_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知产物"):
            ArtifactToolSet(
                self.project_path,
                output_artifact="unknown",
            )

    def test_store_supports_delivery_test_and_review_artifacts(self) -> None:
        store = ArtifactStore(self.project_path)

        store.save("tests", "# Tests")
        store.save("review", "# Review")

        self.assertEqual(store.load("tests"), "# Tests")
        self.assertEqual(store.load("review"), "# Review")

    def test_toolset_can_return_bounded_excerpt_for_review_style_reads(self) -> None:
        store = ArtifactStore(self.project_path)
        store.save("requirement", "A" * 500 + "B" * 500)
        tools = ArtifactToolSet(
            self.project_path,
            output_artifact="review",
            readable_artifacts=("requirement",),
            read_char_limit=200,
        )

        excerpt = tools.load("requirement")

        self.assertTrue(excerpt.startswith("A" * 100))
        self.assertTrue(excerpt.endswith("B" * 100))
        self.assertIn("中间已省略", excerpt)


if __name__ == "__main__":
    unittest.main()

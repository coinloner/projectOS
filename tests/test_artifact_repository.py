import tempfile
import unittest
from pathlib import Path

from app.artifact.repository import ArtifactRef, ArtifactRepository


class ArtifactRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.project_path = self._directory.name
        self.repository = ArtifactRepository(self.project_path)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_staging_isolated_from_formal_artifact_until_candidate_is_promoted(self) -> None:
        staged = self.repository.write_staged(
            trace_id="tr-architecture", work_item_id="architecture-api",
            artifact_key="architecture", slot="api", content="# API architecture",
        )

        self.assertEqual(self.repository.load_ref(staged.ref), "# API architecture")
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())

        candidate = self.repository.create_candidate(
            trace_id="tr-architecture", work_item_id="architecture-integration",
            artifact_key="architecture", content="# Integrated architecture",
            source_refs=(staged.ref,),
        )
        self.assertEqual(candidate.report.status, "ready_for_quality_gate")
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())

        current = self.repository.promote_candidate(candidate.id, artifact_key="architecture")

        self.assertEqual(current.ref_id, "published:architecture:rev-001")
        self.assertEqual(
            (Path(self.project_path) / "architecture.md").read_text(encoding="utf-8"),
            "# Integrated architecture",
        )
        self.assertEqual(self.repository.load_ref(current), "# Integrated architecture")

    def test_rejected_candidate_never_changes_current_revision(self) -> None:
        self.repository.write_staged(
            trace_id="tr-first", work_item_id="architecture-overview",
            artifact_key="architecture", slot="overview", content="# Baseline",
        )
        first_ref = ArtifactRef.staged(
            artifact_key="architecture", trace_id="tr-first",
            work_item_id="architecture-overview", slot="overview",
        )
        accepted = self.repository.create_candidate(
            trace_id="tr-first", work_item_id="architecture-integration",
            artifact_key="architecture", content="# Published", source_refs=(first_ref,),
        )
        self.repository.promote_candidate(accepted.id, artifact_key="architecture")

        rejected = self.repository.create_candidate(
            trace_id="tr-second", work_item_id="architecture-integration",
            artifact_key="architecture", content="# Must not publish", source_refs=(),
        )

        self.assertEqual(rejected.report.status, "needs_rework")
        with self.assertRaisesRegex(PermissionError, "不能发布"):
            self.repository.promote_candidate(rejected.id, artifact_key="architecture")
        self.assertEqual(self.repository.current_ref("architecture").revision_id, "rev-001")
        self.assertEqual(
            (Path(self.project_path) / "architecture.md").read_text(encoding="utf-8"),
            "# Published",
        )

    def test_references_reject_arbitrary_path_like_identifiers(self) -> None:
        with self.assertRaisesRegex(ValueError, "受限 ID"):
            ArtifactRef.staged(
                artifact_key="architecture", trace_id="tr-ok",
                work_item_id="../escape", slot="overview",
            )
        with self.assertRaisesRegex(ValueError, "revision_id"):
            ArtifactRef.published("architecture", "../../escape")


if __name__ == "__main__":
    unittest.main()

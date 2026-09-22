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

    def test_publication_replay_after_projection_failure_reuses_revision(self):
        from unittest.mock import patch

        source = self.repository.write_staged(
            trace_id="tr-replay", work_item_id="source", artifact_key="architecture",
            slot="design", content="# Source",
        ).ref
        candidate = self.repository.create_candidate(
            trace_id="tr-replay", work_item_id="integration", artifact_key="architecture",
            content="# Integrated", source_refs=(source,),
        )
        with patch.object(self.repository._store, "save", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.repository.promote_candidate(candidate.id, artifact_key="architecture")
        with self.assertRaisesRegex(RuntimeError, "投影内容校验失败"):
            self.repository.verify_published_candidate(candidate)
        restored = ArtifactRepository(self.project_path)
        first = restored.current_ref("architecture")
        replayed = restored.promote_candidate(candidate.id, artifact_key="architecture")
        self.assertEqual(first, replayed)
        self.assertEqual(restored.verify_published_candidate(candidate), first)
        self.assertEqual(restored.promote_candidate(candidate.id, artifact_key="architecture"), first)
        revisions = Path(self.project_path) / ".projectos" / "artifacts" / "architecture" / "revisions"
        self.assertEqual(len(list(revisions.glob("rev-*.md"))), 1)
        (Path(self.project_path) / "architecture.md").write_text("corrupt", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            restored.verify_published_candidate(candidate)

    def test_execution_input_change_rejects_staged_and_candidate_writes(self):
        from hashlib import sha256

        upstream = dict(trace_id="tr-snapshot", work_item_id="upstream",
                        artifact_key="architecture", slot="api")
        ref = self.repository.write_staged(**upstream, content="before").ref
        snapshot = (sha256(b"before").hexdigest(),)
        self.repository.write_staged(**upstream, content="after")
        output = dict(trace_id="tr-snapshot", work_item_id="consumer",
                      artifact_key="architecture", content="obsolete result",
                      source_refs=(ref,), expected_source_digests=snapshot)
        with self.assertRaisesRegex(RuntimeError, "执行期间输入已改变"):
            self.repository.write_staged(**output, slot="module")
        with self.assertRaisesRegex(RuntimeError, "执行期间输入已改变"):
            self.repository.create_candidate(**output)
        with self.assertRaises(FileNotFoundError):
            self.repository.load_ref(ArtifactRef.staged(
                artifact_key="architecture", trace_id="tr-snapshot",
                work_item_id="consumer", slot="module"))
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())

    def test_candidate_selection_requires_current_execution_contract(self):
        ref = self.repository.write_staged(
            trace_id="tr-contract", work_item_id="producer",
            artifact_key="architecture", slot="api", content="design",
        ).ref
        identity = dict(trace_id="tr-contract", work_item_id="integration",
                        artifact_key="architecture")
        for digest in (None, "a" * 64):
            self.repository.create_candidate(
                **identity, source_refs=(ref,), content="old candidate",
                contract_digest=digest,
            )
        restored = ArtifactRepository(self.project_path)
        with self.assertRaises(RuntimeError):
            restored.candidate_for_work_item(
                **identity, expected_source_refs=(ref,),
                expected_contract_digest="b" * 64,
            )
        current = restored.create_candidate(
            **identity, source_refs=(ref,), content="current candidate",
            contract_digest="b" * 64,
        )
        selected = ArtifactRepository(self.project_path).candidate_for_work_item(
            **identity, expected_source_refs=(ref,),
            expected_contract_digest="b" * 64,
        )
        self.assertEqual(selected.id, current.id)
        self.assertEqual(selected.contract_digest, "b" * 64)
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())

    def test_changed_source_invalidates_old_candidate_and_allows_new_candidate(self):
        kwargs = dict(trace_id="tr-version", work_item_id="producer", artifact_key="architecture", slot="api")
        ref = self.repository.write_staged(**kwargs, content="version one").ref
        candidate_kwargs = dict(trace_id="tr-version", work_item_id="integration", artifact_key="architecture", source_refs=(ref,))
        old = self.repository.create_candidate(**candidate_kwargs, content="old candidate")
        self.repository.write_staged(**kwargs, content="version two")
        with self.assertRaisesRegex(RuntimeError, "输入已改变"):
            self.repository.promote_candidate(old.id, artifact_key="architecture")
        with self.assertRaises(RuntimeError):
            self.repository.candidate_for_work_item(trace_id="tr-version", work_item_id="integration", artifact_key="architecture")
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())
        new = self.repository.create_candidate(**candidate_kwargs, content="new candidate")
        restored = ArtifactRepository(self.project_path)
        selected = restored.candidate_for_work_item(trace_id="tr-version", work_item_id="integration", artifact_key="architecture")
        self.assertEqual(selected.id, new.id)
        with self.assertRaises(RuntimeError):
            restored.candidate_for_work_item(trace_id="tr-version", work_item_id="integration",
                                             artifact_key="architecture", expected_source_refs=())
        self.assertTrue(selected.source_digests)
        restored.promote_candidate(new.id, artifact_key="architecture")
        self.assertEqual((Path(self.project_path) / "architecture.md").read_text(), "new candidate")

    def test_staged_recovery_rejects_changed_inputs_and_contract(self):
        upstream = dict(trace_id="tr-input", work_item_id="producer",
                        artifact_key="architecture", slot="baseline")
        source = self.repository.write_staged(**upstream, content="v1").ref
        output = dict(trace_id="tr-input", work_item_id="consumer",
                      artifact_key="architecture", slot="design")
        ref = self.repository.write_staged(
            **output, content="design v1", source_refs=(source,), contract_digest="a" * 64,
        ).ref
        expected = dict(expected_trace_id="tr-input", expected_work_item_id="consumer",
                        expected_slot="design", expected_artifact_kind="architecture_markdown",
                        expected_source_refs=(source,), expected_contract_digest="a" * 64)
        restored = ArtifactRepository(self.project_path)
        restored.verify_staged(ref, **expected)
        with self.assertRaisesRegex(RuntimeError, "合同已改变"):
            restored.verify_staged(ref, **{**expected, "expected_contract_digest": "b" * 64})
        with self.assertRaisesRegex(RuntimeError, "输入引用不匹配"):
            restored.verify_staged(ref, **{**expected, "expected_source_refs": ()})
        self.repository.write_staged(**upstream, content="v2")
        with self.assertRaisesRegex(RuntimeError, "输入已改变"):
            restored.verify_staged(ref, **expected)
        self.repository.write_staged(
            **output, content="design v2", source_refs=(source,), contract_digest="a" * 64,
        )
        restored.verify_staged(ref, **expected)

    def test_staged_unknown_provenance_is_not_an_empty_input_set(self):
        output = dict(trace_id="tr-legacy", work_item_id="producer",
                      artifact_key="architecture", slot="design", content="design")
        ref = self.repository.write_staged(**output).ref
        expected = dict(expected_trace_id="tr-legacy", expected_work_item_id="producer",
                        expected_slot="design", expected_artifact_kind="architecture_markdown",
                        expected_source_refs=())
        with self.assertRaisesRegex(RuntimeError, "缺少版本证据"):
            self.repository.verify_staged(ref, **expected)
        self.repository.write_staged(**output, source_refs=())
        self.repository.verify_staged(ref, **expected)

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

    def test_verify_staged_writes_a_receipt_and_recovery_can_revalidate_it(self) -> None:
        staged = self.repository.write_staged(
            trace_id="tr-receipt",
            work_item_id="module-api",
            artifact_key="architecture",
            slot="module-api",
            content="# Module API",
        )

        receipt = self.repository.verify_staged(
            staged.ref,
            expected_trace_id="tr-receipt",
            expected_work_item_id="module-api",
            expected_slot="module-api",
            expected_artifact_kind="architecture_design",
            validator=lambda content: content,
        )
        restored = self.repository.load_commit_receipt(staged.ref)

        self.assertEqual(receipt.digest, staged.digest)
        self.assertEqual(restored.artifact_kind, "architecture_design")
        self.assertEqual(restored.ref, staged.ref)

    def test_verify_staged_rejects_tampered_content_as_commit_failure(self) -> None:
        staged = self.repository.write_staged(
            trace_id="tr-tamper",
            work_item_id="module-api",
            artifact_key="architecture",
            slot="module-api",
            content="# Module API",
        )
        output = Path(self.project_path) / ".projectos" / "runs" / "tr-tamper" / "work-items" / "module-api" / "output" / "module-api.md"
        output.write_text("# Tampered", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "清单校验失败"):
            self.repository.verify_staged(
                staged.ref,
                expected_trace_id="tr-tamper",
                expected_work_item_id="module-api",
                expected_slot="module-api",
                expected_artifact_kind="architecture_design",
            )

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

class IntermediateArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.repository = ArtifactRepository(self._directory.name)

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_intermediate_result_survives_repository_recreation(self):
        saved = self.repository.write_intermediate(
            trace_id="tr-b", work_item_id="module-a", phase="boundary",
            input_digest="input-v1", content="boundary draft",
        )
        restored = ArtifactRepository(self._directory.name).load_intermediate(
            trace_id="tr-b", work_item_id="module-a", phase="boundary",
            expected_input_digest="input-v1",
        )
        self.assertEqual(restored.content, saved.content)
        self.assertEqual(restored.content_digest, saved.content_digest)

    def test_intermediate_result_rejects_changed_input(self):
        self.repository.write_intermediate(
            trace_id="tr-b", work_item_id="module-a", phase="boundary",
            input_digest="input-v1", content="boundary draft",
        )
        with self.assertRaisesRegex(RuntimeError, "输入版本"):
            self.repository.load_intermediate(
                trace_id="tr-b", work_item_id="module-a", phase="boundary",
                expected_input_digest="input-v2",
            )

class IssueReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_path = self._tmp.name
        self.repository = ArtifactRepository(self.project_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_issue_report_is_persisted_as_replan_signal_not_output(self):
        report = self.repository.write_issue_report(
            trace_id="tr-issue", work_item_id="module", requirement_refs=("req-1",),
            parent_artifact_ref="staged:tr-parent:blueprint:blueprint",
            conflicting_constraint="backend requires unavailable persistence contract",
            evidence="interface check failed", reason="constraint cannot be met locally",
            suggested_resolution="revise parent boundary")
        restored = ArtifactRepository(self.project_path).load_issue_report(
            trace_id="tr-issue", work_item_id="module")
        self.assertEqual(restored, report)
        self.assertEqual(report["status"], "needs_replan")
        self.assertFalse((Path(self.project_path) / "architecture.md").exists())

class IntermediateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repository = ArtifactRepository(self._tmp.name)
    def tearDown(self):
        self._tmp.cleanup()
    def test_latest_phase_is_selected_and_changed_input_is_not_reused(self):
        self.repository.write_intermediate(trace_id="tr-b", work_item_id="module", phase="boundaries", input_digest="a"*64, content="b")
        self.repository.write_intermediate(trace_id="tr-b", work_item_id="module", phase="interfaces", input_digest="a"*64, content="i")
        latest = self.repository.latest_intermediate(trace_id="tr-b", work_item_id="module", phases=("boundaries", "interfaces"), expected_input_digest="a"*64)
        self.assertEqual(latest.phase, "interfaces")
        with self.assertRaises(RuntimeError):
            self.repository.latest_intermediate(trace_id="tr-b", work_item_id="module", phases=("boundaries", "interfaces"), expected_input_digest="c"*64)

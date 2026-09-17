import tempfile
import unittest

from app.orchestration.retry import (
    FailureKind,
    FailureSignal,
    RecoveryAction,
    RetryLedger,
    RetryRecord,
    RetryScope,
    RetryPolicy,
)


class RetryPolicyTest(unittest.TestCase):
    def test_nonretryable_failure_blocks_even_with_available_budget(self):
        signal = FailureSignal(FailureKind.PLANNER_VALIDATION, "upstream missing", retryable=False)
        self.assertEqual(FailureSignal.from_dict(signal.as_dict()), signal)
        self.assertEqual(RetryPolicy().action_for(signal, total_retries=0, item_retries=0, kind_retries=0), RecoveryAction.BLOCK)

    def test_retry_ledger_round_trips_and_counts_parallel_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as project_path:
            signal = FailureSignal(FailureKind.PROVIDER_STALL, "provider stalled", input_digest="contract")
            ledger = RetryLedger(project_path, "tr-test123")
            record = RetryRecord(
                scope=RetryScope.BATCH,
                subject_id="batch-w0-a-b",
                attempt=1,
                max_attempts=2,
                action=RecoveryAction.RETRY_BATCH,
                failure=signal,
                root_work_item_id="a",
                interrupted_work_item_ids=("b",),
            )
            ledger.append(record)

            restored = RetryLedger(project_path, "tr-test123")
            self.assertEqual(restored.records(), (record,))
            self.assertEqual(restored.budget_count(), 1)
            # Interrupted siblings resume, but they did not cause the
            # failure and therefore must not consume their own retry budget.
            self.assertEqual(restored.budget_count_for_work_item("b"), 0)
            self.assertEqual(restored.budget_count_for_kind("a", FailureKind.PROVIDER_STALL), 1)
            self.assertTrue(
                restored.has_same_failure_without_new_evidence(
                    work_item_id="a", signal=signal
                )
            )
            self.assertEqual(restored.latest_for_work_item("b"), record)

    def test_control_plane_contract_failures_have_explicit_retry_boundaries(self) -> None:
        policy = RetryPolicy()

        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.TOOL_CONTRACT_MISMATCH, "missing tool"),
                total_retries=0, item_retries=0, kind_retries=0,
            ),
            RecoveryAction.BLOCK,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.ARTIFACT_CONTRACT_VIOLATION, "invalid staged output"),
                total_retries=0, item_retries=0, kind_retries=0,
            ),
            RecoveryAction.RETRY_ITEM,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.ARTIFACT_CONTRACT_VIOLATION, "invalid staged output"),
                total_retries=1, item_retries=1, kind_retries=1,
            ),
            RecoveryAction.FAIL,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.ARTIFACT_COMMIT_FAILURE, "digest mismatch"),
                total_retries=0, item_retries=0, kind_retries=0,
            ),
            RecoveryAction.BLOCK,
        )

    def test_policy_chooses_recovery_by_failure_cause_and_budget(self) -> None:
        policy = RetryPolicy()

        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.TEST_FAILURE, "assertion failed"),
                total_retries=0,
                item_retries=0,
                kind_retries=0,
            ),
            RecoveryAction.REQUEST_REPLAN,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.SANDBOX_SETUP, "cache missing"),
                total_retries=0,
                item_retries=0,
                kind_retries=0,
            ),
            RecoveryAction.BLOCK,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.SANDBOX_TIMEOUT, "timed out"),
                total_retries=0,
                item_retries=0,
                kind_retries=0,
            ),
            RecoveryAction.RETRY_ITEM,
        )
        self.assertEqual(
            policy.action_for(
                FailureSignal(FailureKind.SANDBOX_TIMEOUT, "timed out"),
                total_retries=1,
                item_retries=1,
                kind_retries=1,
            ),
            RecoveryAction.FAIL,
        )


if __name__ == "__main__":
    unittest.main()

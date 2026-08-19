import unittest

from app.orchestration.retry import (
    FailureKind,
    FailureSignal,
    RecoveryAction,
    RetryPolicy,
)


class RetryPolicyTest(unittest.TestCase):
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

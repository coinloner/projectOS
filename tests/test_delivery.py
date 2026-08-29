import tempfile
import unittest

from app.orchestration.delivery import DeliveryState, DeliveryStore, RequirementRecord, TraceabilityMatrix


class DeliveryStoreTest(unittest.TestCase):
    def test_state_machine_rejects_skipping_and_records_valid_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DeliveryStore(directory)
            with self.assertRaises(ValueError):
                store.transition(DeliveryState.DELIVERY_READY, reason="skip")
            store.transition(DeliveryState.IMPLEMENTING, reason="started")
            store.transition(DeliveryState.BUILT, reason="built")
            self.assertEqual(store.state(), DeliveryState.BUILT)

    def test_traceability_matrix_marks_missing_evidence(self) -> None:
        matrix = TraceabilityMatrix()
        matrix.register(RequirementRecord("AC-1", "用户可以创建订单", ("AC-1",)))
        matrix.bind_implementation(("AC-1",), "backend-api")
        self.assertEqual(matrix.incomplete(), ("AC-1",))
        record = matrix.requirements["AC-1"]
        matrix.requirements["AC-1"] = RequirementRecord(
            **{**record.__dict__, "test_evidence_ids": ("ev-test",), "runtime_evidence_ids": ("ev-runtime",)}
        )
        self.assertEqual(matrix.incomplete(), ())

    def test_initialize_from_markdown_accepts_numbered_bold_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DeliveryStore(directory)
            matrix = store.initialize_from_markdown(
                "## 验收标准\n\n1. **AC-1 活动可创建**\n"
                "2. **AC-2: 票种可配置**\n\n说明：覆盖 AC-1、AC-2。"
            )
            self.assertEqual(tuple(matrix.requirements), ("AC-1", "AC-2"))
            self.assertEqual(matrix.requirements["AC-1"].text, "活动可创建")
            self.assertEqual(matrix.requirements["AC-2"].text, "票种可配置")


if __name__ == "__main__":
    unittest.main()

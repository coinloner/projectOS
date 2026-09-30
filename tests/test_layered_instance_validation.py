import tempfile
import unittest

from scripts.validate_layered_instance import run_layered_validation


class LayeredInstanceValidationTest(unittest.TestCase):
    """L0: deterministic Requirement -> Architecture -> Contract validation."""

    def test_layered_route_publishes_architecture_and_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="projectos-test-layered-") as directory:
            summary = run_layered_validation(directory)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["completed_nodes"], summary["planned_nodes"])
            self.assertTrue(summary["architecture_published"])
            self.assertTrue(summary["contract_published"])
            self.assertEqual(summary["contract_units"], 3)
            self.assertTrue(summary["compiled_work_items"])
            self.assertTrue(summary["contract_digest"])
            self.assertTrue(summary["semantic_loop_completed"])
            self.assertTrue(summary["trace_id"])


if __name__ == "__main__":
    unittest.main()

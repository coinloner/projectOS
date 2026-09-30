import tempfile
import unittest
from pathlib import Path

from scripts.validate_vertical_slice import run_vertical_slice


class VerticalSliceValidationTest(unittest.TestCase):
    """L1: Contract -> CodeAgent-shaped WorkItem -> Git -> Runtime smoke."""

    def test_health_vertical_slice_uses_real_staging_integration_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="projectos-test-vertical-") as directory:
            summary = run_vertical_slice(directory)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["contract_units"], 1)
            self.assertEqual(summary["compiled_work_items"], ["wi-code-health-entrypoint"])
            self.assertEqual(summary["smoke"]["status"], 200)
            self.assertEqual(summary["smoke"]["body"], '{"status":"ok"}')
            self.assertEqual(summary["workspace_file"], "workspace/backend/app/main.py")
            self.assertTrue(summary["changeset_commit"])
            self.assertTrue(summary["baseline"])
            self.assertTrue(summary["code_wave_integrated"])
            self.assertTrue(summary["runtime_smoke_passed"])
            self.assertIn("implementation_plan_compiled", summary["event_types"])
            self.assertIn("code_wave_integrated", summary["event_types"])
            self.assertIn("runtime_smoke_passed", summary["event_types"])


if __name__ == "__main__":
    unittest.main()

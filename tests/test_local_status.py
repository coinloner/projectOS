import json
import tempfile
import unittest
from pathlib import Path

from app.runtime.local_status import LocalRuntimeStatusStore


class LocalRuntimeStatusTest(unittest.TestCase):
    def test_missing_state_is_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = LocalRuntimeStatusStore().read(directory)
            self.assertEqual(result["status"], "not_started")
            self.assertFalse(result["available"])

    def test_state_is_read_without_projectos_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / ".projectos" / "runtime"
            state.mkdir(parents=True)
            (state / "local-run.json").write_text(
                json.dumps({"mode": "local", "status": "stopped", "services": []}),
                encoding="utf-8",
            )
            result = LocalRuntimeStatusStore().read(directory)
            self.assertTrue(result["available"])
            self.assertEqual(result["status"], "stopped")


if __name__ == "__main__":
    unittest.main()

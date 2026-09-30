"""Cross-unit HTTP routes must agree before a wave is published."""

import tempfile
import unittest
from pathlib import Path

from app.domain.architecture.implementation_contract import (
    ImplementationContract, ImplementationContractStore, ImplementationUnit, InterfaceContract,
)
from app.domain.code.git_service import GitCodeIntegrationService
from app.domain.code.http_contract import validate_http_consumers


class HttpContractValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.root = self.project / "workspace"
        self.api_path = self.root / "backend/app/main.py"
        self.tests_path = self.root / "tests/test_records.py"
        self.front_path = self.root / "frontend/src/App.jsx"
        self.contract = ImplementationContract(
            schema_version=1,
            units=(
                ImplementationUnit("api", "transport", "HTTP API", ("backend/app/main.py",),
                                   owned_files=("backend/app/main.py",),
                                   provides_interfaces=("records.api",)),
                ImplementationUnit("tests", "verification", "API tests", ("tests/test_records.py",),
                                   owned_files=("tests/test_records.py",),
                                   consumes_interfaces=("records.api",)),
                ImplementationUnit("frontend", "presentation", "browser UI", ("frontend/src/App.jsx",),
                                   owned_files=("frontend/src/App.jsx",),
                                   consumes_interfaces=("records.api",)),
            ),
            interfaces=(InterfaceContract("records.api", "api", "Records API", "api",
                                          owner_file="backend/app/main.py"),),
        )
        self._write(self.api_path, '''from fastapi import FastAPI
app = FastAPI()
@app.get("/records")
def all_records(): pass
@app.get("/records/statistics")
def statistics(): pass
@app.post("/records")
def create(): pass
@app.patch("/records/{record_id}/status")
def update(record_id: int): pass
@app.delete("/records/{record_id}")
def delete(record_id: int): pass
''')

    @staticmethod
    def _write(path: Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def test_detects_cross_unit_route_drift_and_attributes_consumer(self) -> None:
        self._write(self.tests_path, '''
def test_api(base):
    _ok(base, "GET", "/records/stats")
    _ok(base, "PATCH", f"/records/{record_id}/complete")
''')
        self._write(self.front_path, '''
const RECORDS_PATH = '/api/records'
await request(`${RECORDS_PATH}/stats`)
await request(RECORDS_PATH, {method: 'POST', body: JSON.stringify({title})})
''')
        issues = validate_http_consumers(self.root, self.contract)
        self.assertEqual(len(issues), 4, issues)
        self.assertTrue(any("tests/test_records.py" in issue and "GET /records/stats" in issue for issue in issues))
        self.assertTrue(any("PATCH /records/{parameter}/complete" in issue for issue in issues))
        self.assertTrue(any("frontend/src/App.jsx" in issue and "GET /api/records/stats" in issue for issue in issues))
        self.assertTrue(any("POST /api/records" in issue for issue in issues))
        ImplementationContractStore(str(self.project)).save(self.contract.as_dict())
        with self.assertRaisesRegex(RuntimeError, r"\[owner_files: .*tests/test_records.py.*frontend/src/App.jsx"):
            GitCodeIntegrationService(str(self.project)).validate_published_workspace("trace-http")

    def test_compatible_consumer_routes_and_dynamic_ids_pass(self) -> None:
        self._write(self.tests_path, '''
def test_api(base):
    _ok(base, "GET", "/records/statistics")
    _ok(base, "PATCH", f"/records/{record_id}/status", {"completed": True})
    _ok(base, "DELETE", f"/records/{record_id}")
''')
        self._write(self.front_path, '''
const RECORDS_PATH = '/records'
await request(`${RECORDS_PATH}?status=active`)
await request(`${RECORDS_PATH}/statistics`)
await request(RECORDS_PATH, {method: 'POST'})
await request(`${RECORDS_PATH}/${encodeURIComponent(taskId(record))}/status`, {
  method: 'PATCH', body: JSON.stringify({completed: true}),
})
''')
        self.assertEqual(validate_http_consumers(self.root, self.contract), ())

    def test_does_not_reject_earlier_wave_before_provider_exists(self) -> None:
        self.api_path.unlink()
        self._write(self.tests_path, '_ok(base, "GET", "/records/stats")\n')
        self.assertEqual(validate_http_consumers(self.root, self.contract), ())

    def test_integer_route_does_not_accept_literal_stats_segment(self) -> None:
        self._write(self.tests_path, '_ok(base, "GET", "/records/stats")\n')
        self._write(self.api_path, '''from fastapi import FastAPI
app = FastAPI()
@app.get("/records/{record_id}")
def get(record_id: int): pass
''')
        self.assertIn("GET /records/stats", " ".join(validate_http_consumers(self.root, self.contract)))


if __name__ == "__main__":
    unittest.main()

"""Full deterministic delivery replay gate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.validate_replay_delivery import run_replay_delivery


def test_full_replay_delivery_has_end_to_end_evidence(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker is required for the full delivery replay")
    if not Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file():
        pytest.skip("Chrome is required for browser replay")
    result = run_replay_delivery(tmp_path)
    assert result["status"] == "completed"
    assert result["planned_nodes"] == result["completed_nodes"]
    assert result["compiled_work_items"] == [
        "wi-code-backend-entrypoint",
        "wi-code-frontend-entrypoint",
    ]
    assert all(result["artifacts"].values())
    assert result["api_evidence"]["health"]["status"] == 200
    assert result["api_evidence"]["crud"]["blank_title_status"] == 400
    browser = result["browser_evidence"]
    assert browser["status"] == "passed"
    assert browser["blank_title_feedback"]
    assert len(browser["created"]["items"]) == 1
    assert browser["completed"]["items"][0]["completed"]
    assert browser["active_filter"]["items"] == []
    assert len(browser["completed_filter"]["items"]) == 1
    assert browser["uncompleted"]["items"] == []
    assert browser["deleted"]["items"] == []
    assert browser["deleted"]["summary"] == "0 task(s), 0 completed"
    assert "code_wave_integrated" in result["event_types"]
    assert "sandbox_evidence_recorded" in result["event_types"]
    assert "browser_e2e_passed" in result["event_types"]

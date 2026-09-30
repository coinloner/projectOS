"""Drive a real Wanfa full delivery through the public ProjectOS API.

Unlike the historical external-documentation demo, this is the actual Todo
acceptance target: SQLite persistence, API, browser, tests and review.  This
script never automatically approves optional external capabilities.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.e2e_demo import DemoClient

GOAL = (
    "交付一个小型任务管理 Web 应用，包含 HTTP API、浏览器交互界面和 SQLite 持久化。"
    "支持创建非空标题任务、查询与按 active/completed 状态筛选、完成/取消完成、删除和统计；"
    "空白标题返回 HTTP 400；服务重启后任务数据保留。提供 /health、自动化测试、"
    "Docker sandbox 验证、Review 和启动访问说明。只做以上 MVP，不需要查询外部文档。"
)


def run(api: str, project: str, root: Path, max_wait: int, output: Path) -> dict:
    client = DemoClient(api)
    evidence = {
        "schema_version": 1,
        "project": project,
        "project_path": str((root / project).resolve()),
        "workflow": "delivery_default",
        "provider": "wanfa",
        "model": "gpt-6-sol",
        "goal": GOAL,
        "status": "starting",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    status, response = client.request("POST", "/api/v1/projects", {"name": project})
    if status != 201:
        raise RuntimeError(f"fresh project creation failed: {status}: {response}")
    status, response = client.request("POST", f"/api/v1/projects/{project}/runs", {
        "goal": GOAL, "workflow_id": "delivery_default",
        "architecture_mode": "checkpointed",
        "architecture_candidate_strategy": "incremental_candidate",
        "provider": "wanfa", "model": "gpt-6-sol",
    }, timeout=90)
    if status != 202:
        raise RuntimeError(f"live delivery start failed: {status}: {response}")
    trace_id = response["trace_id"]
    evidence["trace_id"] = trace_id
    evidence["status"] = "running"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    deadline = time.monotonic() + max_wait
    latest = {}
    while time.monotonic() < deadline:
        latest = client.get(f"/api/v1/projects/{project}/runs/{trace_id}")
        progress = latest.get("progress") or {}
        summary = progress.get("summary") or {}
        evidence.update(
            status=latest.get("runtime_status") or latest.get("status"),
            completed_work_item_ids=summary.get("completed_work_item_ids", []),
            failed_work_item_ids=summary.get("failed_work_item_ids", []),
            active_work_item_ids=summary.get("active_work_item_ids", []),
            worker_process_state=latest.get("worker_process_state"),
            last_meaningful_idle_seconds=(progress.get("run") or {}).get("meaningful_idle_seconds"),
        )
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        if evidence["status"] in {"completed", "blocked", "failed", "needs_replan", "waiting_for_capability_approval", "cancelled"}:
            break
        time.sleep(5)
    else:
        evidence["status"] = "timeout"
    events = client.get(f"/api/v1/projects/{project}/runs/{trace_id}/events").get("events", [])
    evidence["event_types"] = [event.get("type") for event in events]
    evidence["last_events"] = events[-20:]
    artifacts = ("requirement", "architecture", "tasks", "environment", "implementation", "tests", "review")
    evidence["artifacts"] = {key: (root / project / f"{key}.md").is_file() for key in artifacts}
    evidence["architecture_contract"] = (root / project / ".projectos/architecture/project-contract.json").is_file()
    evidence["implementation_plan_compilation"] = (root / project / ".projectos/runs" / trace_id / "implementation-plan.json").is_file()
    evidence["trace_status"] = latest.get("status")
    evidence["runtime_status"] = latest.get("runtime_status")
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--project", default=None)
    parser.add_argument("--max-wait", type=int, default=3600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = args.project or f"wanfa-live-l2-{uuid4().hex[:8]}"
    result = run(args.api, project, args.projects_root, args.max_wait, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

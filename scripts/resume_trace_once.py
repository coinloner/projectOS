"""Run one persisted Trace through the real RunService worker lifecycle."""

from __future__ import annotations

import argparse
import time

from app.application.runs import RunCoordinator, RunService
from app.orchestration.trace import TraceStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("trace_id")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument(
        "--source-name",
        default=None,
        help="在恢复动作中批准一个待授权来源（例如 docs-mcp）",
    )
    args = parser.parse_args()

    coordinator = RunCoordinator(max_workers=2)
    try:
        started = RunService(coordinator=coordinator).resume_run(
            project_path=args.project,
            trace_id=args.trace_id,
            source_name=args.source_name,
        )
        print(f"resumed {started}", flush=True)
        traces = TraceStore(args.project)
        deadline = time.monotonic() + args.timeout
        last = None
        terminal = {
            "completed",
            "failed",
            "blocked",
            "waiting_for_capability_approval",
        }
        while time.monotonic() < deadline:
            status = traces.load_trace(args.trace_id).get("status")
            if status != last:
                print(f"status {status}", flush=True)
                last = status
            if status in terminal:
                time.sleep(2)
                print(
                    f"terminal {traces.load_trace(args.trace_id).get('status')}",
                    flush=True,
                )
                return 0
            time.sleep(5)
        print("timeout", flush=True)
        return 2
    finally:
        coordinator.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

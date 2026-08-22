"""Read and refresh the state written by an independent project launcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.sandbox.docker_provider import DockerExecutor, SubprocessDockerExecutor


class LocalRuntimeStatusStore:
    def __init__(self, *, executor: DockerExecutor | None = None) -> None:
        self._executor = executor or SubprocessDockerExecutor()

    def read(self, project_path: str) -> dict[str, Any]:
        path = Path(project_path).resolve() / ".projectos" / "runtime" / "local-run.json"
        if not path.is_file():
            return {"mode": "local", "status": "not_started", "available": False}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return {"mode": "local", "status": "unknown", "available": False, "error": str(error)}
        if not isinstance(payload, dict):
            return {"mode": "local", "status": "unknown", "available": False}
        payload = dict(payload)
        payload["available"] = True
        services = payload.get("services")
        if payload.get("status") == "running" and isinstance(services, list) and services:
            stopped = False
            checked = False
            for service in services:
                if not isinstance(service, dict) or not service.get("container_id"):
                    continue
                checked = True
                result = self._executor.run(
                    ["docker", "inspect", "--format={{.State.Running}}", str(service["container_id"])],
                    timeout_seconds=5,
                )
                if result.exit_code != 0 or result.stdout.strip().lower() != "true":
                    stopped = True
                    break
            if checked and stopped:
                payload["status"] = "stopped"
                payload["monitor_note"] = "独立运行容器已停止"
        return payload


__all__ = ["LocalRuntimeStatusStore"]

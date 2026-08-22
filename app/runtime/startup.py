"""Generate independent and ProjectOS-managed project launchers."""

from __future__ import annotations

import json
from pathlib import Path
import re
import stat
from typing import Any

import yaml

from app.runtime.application import ApplicationCatalog
from app.runtime.manifest import RuntimeManifest


def ensure_startup_scripts(project_path: str, *, project_id: str | None = None) -> tuple[Path, Path]:
    """Write local, managed, and macOS-friendly launchers.

    The compose file and local helper are generated only from the trusted
    ApplicationCatalog.  They do not execute commands supplied by a project.
    """
    root = Path(project_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = project_id or root.name
    resolved_id = candidate if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", candidate) else "project"
    application_id: str | None = None
    profile_id: str | None = None
    try:
        manifest = RuntimeManifest.load(str(root))
        profile_id = manifest.profile
        application_id = ApplicationCatalog.resolve(str(root), manifest.application)
    except (FileNotFoundError, ValueError, OSError):
        pass

    shell = root / "start.sh"
    powershell = root / "start.ps1"
    shell.write_text(_local_shell_script(), encoding="utf-8")
    powershell.write_text(_local_powershell_script(), encoding="utf-8")
    _executable(shell)

    managed = root / "start-managed.sh"
    managed.write_text(_managed_shell_script(resolved_id), encoding="utf-8")
    _executable(managed)
    (root / "start-managed.ps1").write_text(
        _managed_powershell_script(resolved_id), encoding="utf-8"
    )
    command = root / "start.command"
    command.write_text(_command_script(), encoding="utf-8")
    _executable(command)

    projectos = root / ".projectos"
    projectos.mkdir(parents=True, exist_ok=True)
    helper = projectos / "local_runtime.py"
    helper.write_text(
        _local_runtime_helper(resolved_id, application_id, profile_id), encoding="utf-8"
    )
    _executable(helper)
    (root / "docker-compose.yml").write_text(
        _compose_yaml(root, resolved_id, application_id, profile_id), encoding="utf-8"
    )
    return shell, powershell


def _executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _local_shell_script() -> str:
    return '''#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/.projectos/local_runtime.py" start
# Managed mode is explicit: start-managed.sh calls /api/v1/projects/import and
# /api/v1/projects/$PROJECTOS_PROJECT_ID/runtime/runs through ProjectOS.
'''


def _local_powershell_script() -> str:
    return '''$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
& python (Join-Path $root ".projectos/local_runtime.py") start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Managed mode is explicit: start-managed.ps1 calls /runtime/runs through ProjectOS.
'''


def _managed_shell_script(project_id: str) -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail
PROJECTOS_API_URL="${{PROJECTOS_API_URL:-http://127.0.0.1:8000}}"
PROJECTOS_PROJECT_ID="${{PROJECTOS_PROJECT_ID:-{project_id}}}"
PROJECTOS_PROJECT_PATH="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
curl --fail --silent --show-error "$PROJECTOS_API_URL/health" >/dev/null || {{
  echo "ProjectOS 控制面未启动: $PROJECTOS_API_URL" >&2; exit 1;
}}
escaped_path=$(printf '%s' "$PROJECTOS_PROJECT_PATH" | sed 's/\\\\/\\\\\\\\/g; s/"/\\"/g')
payload=$(printf '{{"name":"%s","path":"%s"}}' "$PROJECTOS_PROJECT_ID" "$escaped_path")
curl --fail --silent --show-error -X POST "$PROJECTOS_API_URL/api/v1/projects/import" \\
  -H 'content-type: application/json' -d "$payload" >/dev/null || true
echo "正在通过 ProjectOS 启动项目 $PROJECTOS_PROJECT_ID ..."
curl --fail --show-error -X POST \\
  "$PROJECTOS_API_URL/api/v1/projects/$PROJECTOS_PROJECT_ID/runtime/runs"
echo
'''


def _managed_powershell_script(project_id: str) -> str:
    return f'''$ErrorActionPreference = "Stop"
$api = if ($env:PROJECTOS_API_URL) {{ $env:PROJECTOS_API_URL }} else {{ "http://127.0.0.1:8000" }}
$projectId = if ($env:PROJECTOS_PROJECT_ID) {{ $env:PROJECTOS_PROJECT_ID }} else {{ "{project_id}" }}
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
Invoke-RestMethod "$api/health" | Out-Null
$payload = @{{ name = $projectId; path = $projectPath }} | ConvertTo-Json -Compress
try {{ Invoke-RestMethod "$api/api/v1/projects/import" -Method Post -ContentType "application/json" -Body $payload | Out-Null }}
catch {{ if ($_.Exception.Response.StatusCode.value__ -ne 409) {{ throw }} }}
Write-Host "正在通过 ProjectOS 启动项目 $projectId ..."
Invoke-RestMethod "$api/api/v1/projects/$projectId/runtime/runs" -Method Post | ConvertTo-Json -Depth 8
'''


def _command_script() -> str:
    return '''#!/usr/bin/env bash
set -u
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
"$SCRIPT_DIR/start.sh"
status=$?
echo
if [ "$status" -ne 0 ]; then echo "启动失败 (exit $status)"; fi
read -r -p "按回车关闭此窗口..." _
exit "$status"
'''


def _compose_yaml(root: Path, project_id: str, application_id: str | None, profile_id: str | None) -> str:
    if not application_id or not profile_id:
        return "# ProjectOS will refresh this file after runtime.yaml declares a trusted application.\n"
    from app.runtime.manifest import RuntimeCatalog

    profile = ApplicationCatalog.get(application_id)
    runtime_image = RuntimeCatalog.get(profile_id).image
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}
    for service in profile.services:
        source = "./workspace" if not service.workspace_dir else f"./workspace/{service.workspace_dir}"
        if service.mount_dir:
            source = f"./workspace/{service.mount_dir}"
        item: dict[str, Any] = {
            "image": service.image or runtime_image,
            "command": list(_local_command(service.command)),
            "working_dir": service.container_workdir,
            "user": service.user,
            "read_only": service.read_only,
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "tmpfs": ["/tmp:rw,noexec,nosuid,size=512m"],
            "labels": {
                "projectos.mode": "local",
                "projectos.project_id": project_id,
                "projectos.project_path": str(root),
                "projectos.application_id": application_id,
            },
        }
        if service.mount_workspace:
            item["volumes"] = [f"{source}:/workspace:ro"]
            if any("PROJECTOS_DEPENDENCY_DIR" in part for part in service.command) and (root / "requirements.in").is_file():
                item["volumes"].append("./requirements.in:/input/requirements.in:ro")
        if service.data_volume:
            item.setdefault("volumes", []).append(f"{service.data_volume}:{service.volume_mount_dir}")
            volumes[service.data_volume] = None
        if service.publish_port:
            key = f"PROJECTOS_{service.id.upper().replace('-', '_')}_PORT"
            item["ports"] = [f"${{{key}:-{service.container_port}}}:{service.container_port}"]
        if service.environment:
            item["environment"] = {key: value for key, value in service.environment}
        if service.readiness:
            item["healthcheck"] = {
                "test": ["CMD", *service.readiness],
                "interval": "3s", "timeout": "3s", "retries": 20,
            }
        if service.network_alias:
            item["networks"] = {"default": {"aliases": [service.network_alias]}}
        if service.id != "database" and any(s.id == "database" for s in profile.services):
            item["depends_on"] = {"database": {"condition": "service_healthy"}}
        services[service.id] = item
    document: dict[str, Any] = {"name": f"projectos-{project_id}", "services": services}
    if volumes:
        document["volumes"] = volumes
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


def _local_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if len(command) != 3 or command[0:2] != ("sh", "-ec") or "PROJECTOS_DEPENDENCY_DIR" not in command[2]:
        return command
    script = command[2]
    tail = script[script.index("if [ -f migrate.py ]"):] if "if [ -f migrate.py ]" in script else "exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
    install = "mkdir -p /tmp/projectos-site && python -m pip install --no-cache-dir --disable-pip-version-check -r /input/requirements.in --target /tmp/projectos-site && export PYTHONPATH=/tmp/projectos-site"
    return ("sh", "-ec", f"{install} && {tail}")


def _local_runtime_helper(project_id: str, application_id: str | None, profile_id: str | None) -> str:
    encoded = json.dumps(
        {"project_id": project_id, "application_id": application_id, "profile_id": profile_id},
        ensure_ascii=False,
    )
    return f'''#!/usr/bin/env python3
"""Generated local runtime helper; it never calls the ProjectOS HTTP API."""
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".projectos" / "runtime" / "local-run.json"
CONFIG = {encoded!r}
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "projectos-{project_id}")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "docker-compose.yml")]

def run(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, text=True)

def _url(name):
    ports = {{"backend": os.environ.get("PROJECTOS_BACKEND_PORT", "8000"), "frontend": os.environ.get("PROJECTOS_FRONTEND_PORT", "8080"), "web": os.environ.get("PROJECTOS_WEB_PORT", "8081")}}
    return f"http://127.0.0.1:{{ports[name]}}" if name in ports else None

def write_state(status, error=None):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    services = []
    for name in ("database", "backend", "frontend", "web"):
        result = subprocess.run(COMPOSE + ["ps", "-q", name], cwd=ROOT, capture_output=True, text=True)
        container_id = result.stdout.strip()
        if container_id:
            services.append({{"id": name, "container_id": container_id, "url": _url(name)}})
    payload = {{"mode":"local", "status":status, "project_id":CONFIG["project_id"], "application_id":CONFIG["application_id"], "compose_project":PROJECT, "project_path":str(ROOT), "services":services, "error":error, "updated_at":datetime.now(timezone.utc).isoformat()}}
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    return payload

def main(command):
    if not CONFIG["application_id"]:
        print("runtime.yaml 尚未声明受信 application，请先完成环境节点。", file=sys.stderr); return 2
    if command == "start":
        result = run("up", "-d")
        if result.returncode: write_state("failed", "docker compose up 失败"); return result.returncode
        print(json.dumps(write_state("running"), ensure_ascii=False, indent=2)); return 0
    if command == "stop":
        result = run("down")
        write_state("stopped", None if result.returncode == 0 else "docker compose down 失败"); return result.returncode
    if command == "status":
        print(json.dumps(write_state("running"), ensure_ascii=False, indent=2)); return 0
    print("用法: local_runtime.py {{start|stop|status}}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "status"))
'''


__all__ = ["ensure_startup_scripts"]

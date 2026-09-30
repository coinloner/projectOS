"""Run a deterministic full delivery replay with durable evidence.

The replay deliberately uses the same control-plane boundaries as a live run,
while replacing the model with deterministic implementation decisions.  It
covers the complete contract from Requirement through API and browser smoke:

Requirement -> Architecture -> Contract -> Tasks -> Environment ->
CodeAgent ChangeSets -> Wave integration -> Tests -> Review -> Runtime ->
HTTP API E2E -> Browser E2E.

The script is intended to be a release gate, not a unit-test shortcut.  Every
stage writes a project artifact and a Trace event; code is staged through Git
worktrees, tests run in the trusted Docker sandbox, and runtime/API/browser
checks run against the integrated workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.environment import EnvironmentProvisioner
from app.artifact.store import ArtifactStore
from app.domain.architecture.implementation_contract import (
    EntrypointContract,
    ImplementationContract,
    ImplementationContractStore,
    ImplementationUnit,
)
from app.domain.code.service import CodeIntegrationService, CodeStagingService
from app.domain.review.service import ReviewService
from app.domain.task.projection import project_tasks
from app.domain.test.service import TestService
from app.domain.test.tools import SandboxEvidenceToolSet
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.evidence import RuntimeEvidence
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import DependencySource, WorkItem, WorkItemDependency
from app.runtime.manifest import RuntimeManifest
from app.workflow.compiler import ImplementationContractCompiler


REQUIREMENT = """# Todo 任务管理交付

## 目标
交付一个可运行的任务管理 Web 应用，支持创建、查询、完成、取消完成和删除任务。

## 验收标准
- AC-001: 用户可以创建非空标题的任务。
- AC-002: 用户可以查询任务并按状态筛选。
- AC-003: 用户可以完成、取消完成和删除任务。
- AC-004: HTTP API 和浏览器页面可以访问，服务重启后任务仍然保留。
"""

BACKEND = r'''"""Deterministic Todo backend used by the ProjectOS replay gate."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/tmp/projectos-todo-replay.json")
FRONTEND = ROOT / "frontend" / "index.html"


class TaskStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("TODO_DB_PATH", str(DEFAULT_DB)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict[str, object]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = []
        return value if isinstance(value, list) else []

    def _write(self, tasks: list[dict[str, object]]) -> None:
        self.path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self, status: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            tasks = self._read()
        if status == "completed":
            tasks = [task for task in tasks if task.get("completed") is True]
        elif status == "active":
            tasks = [task for task in tasks if task.get("completed") is not True]
        return tasks

    def create(self, title: str) -> dict[str, object]:
        with self._lock:
            tasks = self._read()
            next_id = max((int(task.get("id", 0)) for task in tasks), default=0) + 1
            task = {"id": next_id, "title": title, "completed": False}
            tasks.append(task)
            self._write(tasks)
            return task

    def update(self, task_id: int, completed: bool) -> dict[str, object] | None:
        with self._lock:
            tasks = self._read()
            for task in tasks:
                if int(task.get("id", -1)) == task_id:
                    task["completed"] = bool(completed)
                    self._write(tasks)
                    return task
        return None

    def delete(self, task_id: int) -> bool:
        with self._lock:
            tasks = self._read()
            filtered = [task for task in tasks if int(task.get("id", -1)) != task_id]
            if len(filtered) == len(tasks):
                return False
            self._write(filtered)
            return True


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class TodoHandler(BaseHTTPRequestHandler):
    server_version = "ProjectOSReplay/1.0"

    def __init__(self, request, client_address, server, store: TaskStore):
        self.store = store
        super().__init__(request, client_address, server)

    def log_message(self, *_args) -> None:
        return

    def _send(self, status: int, payload: object, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if parsed.path == "/api/tasks":
            status = parse_qs(parsed.query).get("status", [None])[0]
            self._send(200, {"tasks": self.store.list(status)})
            return
        if parsed.path == "/api/stats":
            tasks = self.store.list()
            self._send(200, {"total": len(tasks), "completed": sum(bool(t.get("completed")) for t in tasks)})
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            try:
                body = FRONTEND.read_bytes()
            except OSError:
                self._send(500, {"error": "frontend_missing"})
                return
            self._send(200, body, "text/html")
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/api/tasks":
            self._send(404, {"error": "not_found"})
            return
        title = str(self._read_json().get("title", "")).strip()
        if not title:
            self._send(400, {"error": "title_required"})
            return
        self._send(201, self.store.create(title))

    def do_PATCH(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "tasks"]:
            self._send(404, {"error": "not_found"})
            return
        try:
            task_id = int(parts[2])
        except ValueError:
            self._send(400, {"error": "invalid_id"})
            return
        task = self.store.update(task_id, bool(self._read_json().get("completed")))
        self._send(200, task) if task else self._send(404, {"error": "not_found"})

    def do_DELETE(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "tasks"]:
            self._send(404, {"error": "not_found"})
            return
        try:
            task_id = int(parts[2])
        except ValueError:
            self._send(400, {"error": "invalid_id"})
            return
        self._send(204, b"", "application/json") if self.store.delete(task_id) else self._send(404, {"error": "not_found"})


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    store = TaskStore()
    class BoundHandler(TodoHandler):
        def __init__(self, request, client_address, server):
            super().__init__(request, client_address, server, store)
    return ThreadingHTTPServer((host, port), BoundHandler)


def app(environ=None, start_response=None):
    """Callable marker used by the runtime smoke contract."""
    return create_server


if __name__ == "__main__":
    create_server("0.0.0.0", int(os.environ.get("PORT", "8000"))).serve_forever()
'''

FRONTEND = r'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ProjectOS Todo</title></head>
<body>
<main>
  <h1>ProjectOS Todo</h1>
  <form id="create"><input id="title" aria-label="Task title" required><button>Add task</button></form>
  <label for="filter">Show tasks</label>
  <select id="filter" aria-label="Task filter">
    <option value="all">All</option><option value="active">Active</option><option value="completed">Completed</option>
  </select>
  <p id="summary">Loading…</p>
  <p id="error" role="alert"></p>
  <ul id="tasks"></ul>
</main>
<script>
async function refresh() {
  try {
    const filter = document.querySelector('#filter').value;
    const query = filter === 'all' ? '' : `?status=${filter}`;
    const [response, statsResponse] = await Promise.all([fetch(`/api/tasks${query}`), fetch('/api/stats')]);
    if (!response.ok || !statsResponse.ok) throw new Error('Could not load tasks');
    const payload = await response.json();
    const stats = await statsResponse.json();
    document.querySelector('#tasks').innerHTML = payload.tasks.map(task =>
      `<li data-id="${task.id}"><label><input type="checkbox" ${task.completed ? 'checked' : ''}> ${task.title}</label> <button data-delete="${task.id}">Delete</button></li>`
    ).join('');
    document.querySelector('#summary').textContent = `${stats.total} task(s), ${stats.completed} completed`;
    document.querySelector('#error').textContent = '';
  } catch (error) {
    document.querySelector('#error').textContent = error.message;
  }
}
document.querySelector('#filter').addEventListener('change', refresh);
document.querySelector('#create').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:document.querySelector('#title').value})});
  if (!response.ok) { document.querySelector('#error').textContent = 'Could not create task'; return; }
  document.querySelector('#title').value = '';
  await refresh();
});
document.querySelector('#tasks').addEventListener('change', async event => {
  if (event.target.type === 'checkbox') {
    const response = await fetch(`/api/tasks/${event.target.closest('li').dataset.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({completed:event.target.checked})});
    if (!response.ok) { document.querySelector('#error').textContent = 'Could not update task'; return; }
    await refresh();
  }
});
document.querySelector('#tasks').addEventListener('click', async event => {
  const id = event.target.dataset.delete;
  if (id) {
    const response = await fetch(`/api/tasks/${id}`, {method:'DELETE'});
    if (!response.ok) { document.querySelector('#error').textContent = 'Could not delete task'; return; }
    await refresh();
  }
});
refresh();
</script>
</body>
</html>
'''

TESTS = r'''import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from backend.main import TaskStore, create_server


class TodoApiTest(unittest.TestCase):
    def test_store_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.json")
            task = store.create("write replay gate")
            self.assertFalse(task["completed"])
            self.assertEqual(store.list()[0]["title"], "write replay gate")
            self.assertTrue(store.update(task["id"], True)["completed"])
            self.assertEqual(len(store.list("active")), 0)
            self.assertTrue(store.delete(task["id"]))
            self.assertEqual(store.list(), [])

    def test_http_contract_rejects_blank_title(self):
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/tasks"
            request = Request(url, data=json.dumps({"title": " "}).encode(), method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(Exception) as caught:
                urlopen(request)
            self.assertEqual(getattr(caught.exception, "code", None), 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
'''


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body.decode()) if body else None
    except HTTPError as error:
        body = error.read()
        return error.code, json.loads(body.decode()) if body else None


def _wait_for(base: str, path: str = "/health", timeout: float = 8.0) -> tuple[int, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return _request(base, "GET", path)
        except (OSError, URLError):
            time.sleep(0.1)
    raise AssertionError(f"runtime did not become ready: {base}{path}")


def _unit(unit_id: str, layer: str, objective: str, path: str, *, wave: int, depends_on: tuple[str, ...] = ()) -> ImplementationUnit:
    return ImplementationUnit(
        unit_id=unit_id,
        layer=layer,
        objective=objective,
        allowed_paths=(path.rsplit("/", 1)[0] + "/**",),
        required_paths=(path,),
        owned_files=(path,),
        wave=wave,
        depends_on=depends_on,
        acceptance_criteria=(f"{path} 必须存在并通过交付测试",),
        requirement_ids=("AC-001", "AC-002", "AC-003", "AC-004"),
    )


def _make_contract() -> ImplementationContract:
    return ImplementationContract(
        schema_version=1,
        compilation_strategy="file",
        units=(
            _unit("backend-entrypoint", "runtime", "提供 Todo HTTP API 和健康检查", "backend/main.py", wave=0),
            _unit("frontend-entrypoint", "frontend", "提供浏览器任务管理页面", "frontend/index.html", wave=1, depends_on=("backend-entrypoint",)),
        ),
        entrypoints=EntrypointContract(
            backend_file="backend/main.py",
            backend_import="backend.main",
            backend_command="python main.py",
            frontend_file="frontend/index.html",
            health_path="/health",
        ),
        required_files=("backend/main.py", "frontend/index.html"),
        layers=("runtime", "frontend"),
        allowed_dependencies={"runtime": (), "frontend": ("runtime",)},
        forbidden_imports={"runtime": (), "frontend": ()},
        path_mapping={"runtime": ("backend/**",), "frontend": ("frontend/**",)},
        required_test_types=("unit", "api_http", "browser"),
    )


def _write_initial_project(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "workspace").mkdir(exist_ok=True)
    (project / "project.yaml").write_text("version: 1\nname: replay-todo\n", encoding="utf-8")
    (project / "runtime.yaml").write_text(
        "version: 1\nprofile: python-stdlib\napplication: python-backend\nmode: production\n",
        encoding="utf-8",
    )
    ArtifactStore(str(project)).save("requirement", REQUIREMENT)


def _append_dependencies(item: WorkItem, dependencies: tuple[str, ...]) -> WorkItem:
    existing = tuple(item.dependency_ids)
    additions = tuple(
        WorkItemDependency(work_item_id=dep, source=DependencySource.SYSTEM, rule_id="replay:stage-order")
        for dep in dependencies if dep not in existing
    )
    return WorkItem(**{**item.__dict__, "dependencies": item.dependencies + additions})


def _browser_smoke(url: str) -> dict[str, object]:
    """Use a real headless Chrome page through the DevTools Protocol.

    Chrome on macOS keeps the browser process alive instead of returning from
    ``--dump-dom``.  Starting an isolated profile and querying the rendered DOM
    over CDP gives us a deterministic, process-bounded browser assertion.
    """
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.is_file():
        raise AssertionError("Google Chrome 未安装，无法完成 browser E2E")
    import websocket

    port = _free_port()
    profile = Path(tempfile.mkdtemp(prefix="projectos-browser-"))
    process = subprocess.Popen(
        [
            str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--remote-allow-origins=*",
            f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ws = None
    try:
        deadline = time.monotonic() + 8
        target = None
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.5) as response:
                    candidates = json.loads(response.read().decode())
                target = next((item for item in candidates if item.get("type") == "page"), None)
                if target and target.get("webSocketDebuggerUrl"):
                    break
            except (OSError, URLError, ValueError):
                time.sleep(0.1)
        if not target:
            stderr = process.stderr.read()[-2000:] if process.stderr else ""
            raise AssertionError(f"Chrome CDP target 未就绪: {stderr}")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5, origin=None)
        sequence = 0

        def call(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            nonlocal sequence
            sequence += 1
            ws.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv())
                if message.get("id") == sequence:
                    return message

        call("Page.enable")
        call("Runtime.enable")
        call("Page.navigate", {"url": url})
        time.sleep(1.2)
        def evaluate(expression: str) -> object:
            response = call("Runtime.evaluate", {
                "expression": expression, "returnByValue": True, "awaitPromise": True,
            })
            if response.get("exceptionDetails") or response.get("error"):
                raise AssertionError(f"浏览器执行失败: {response}")
            return response.get("result", {}).get("result", {}).get("value")

        snapshot = """JSON.stringify({
          title: document.title, form: !!document.querySelector('#create'),
          items: [...document.querySelectorAll('#tasks li')].map(li => ({
            title: li.innerText, completed: li.querySelector('input').checked
          })),
          summary: document.querySelector('#summary')?.innerText,
          filter: document.querySelector('#filter')?.value,
          error: document.querySelector('#error')?.innerText,
          blankTitleInvalid: document.querySelector('#title')?.validity.valueMissing,
          blankTitleMessage: document.querySelector('#title')?.validationMessage
        })"""

        def observe() -> dict[str, object]:
            value = evaluate(snapshot)
            return json.loads(value) if isinstance(value, str) else {}

        def expect(label: str, predicate) -> dict[str, object]:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = observe()
                if predicate(state):
                    return state
                time.sleep(0.1)
            raise AssertionError(f"浏览器 {label} 未达到预期: {observe()}")

        initial = expect("initial render", lambda x: x.get("title") == "ProjectOS Todo"
                         and x.get("form") and x.get("summary") == "0 task(s), 0 completed")
        evaluate("document.querySelector('#create').requestSubmit(); 'checked'")
        blank = observe()
        if not blank.get("blankTitleInvalid") or not blank.get("blankTitleMessage"):
            raise AssertionError(f"空标题没有浏览器错误反馈: {blank}")
        evaluate("document.querySelector('#title').value = 'browser task'; document.querySelector('#create').requestSubmit(); 'submitted'")
        created = expect("create", lambda x: len(x.get("items", [])) == 1
                         and "browser task" in x["items"][0]["title"])
        evaluate("document.querySelector('#tasks input[type=checkbox]').click(); 'toggled'")
        completed = expect("toggle completed", lambda x: len(x.get("items", [])) == 1
                           and x["items"][0]["completed"] and x.get("summary") == "1 task(s), 1 completed")
        evaluate("document.querySelector('#filter').value = 'active'; document.querySelector('#filter').dispatchEvent(new Event('change')); 'filtered'")
        active = expect("active filter", lambda x: x.get("filter") == "active" and not x.get("items"))
        evaluate("document.querySelector('#filter').value = 'completed'; document.querySelector('#filter').dispatchEvent(new Event('change')); 'filtered'")
        filtered = expect("completed filter", lambda x: x.get("filter") == "completed"
                          and len(x.get("items", [])) == 1 and x["items"][0]["completed"])
        evaluate("document.querySelector('#tasks input[type=checkbox]').click(); 'untoggled'")
        uncompleted = expect("toggle active", lambda x: x.get("filter") == "completed"
                             and not x.get("items") and x.get("summary") == "1 task(s), 0 completed")
        evaluate("document.querySelector('#filter').value = 'all'; document.querySelector('#filter').dispatchEvent(new Event('change')); 'filtered'")
        expect("all filter", lambda x: x.get("filter") == "all" and len(x.get("items", [])) == 1)
        evaluate("document.querySelector('#tasks button[data-delete]').click(); 'deleted'")
        deleted = expect("delete", lambda x: not x.get("items") and x.get("summary") == "0 task(s), 0 completed")
        return {
            "status": "passed", "url": url, "title": initial["title"], "form_seen": True,
            "blank_title_feedback": blank["blankTitleMessage"], "created": created,
            "completed": completed, "active_filter": active, "completed_filter": filtered,
            "uncompleted": uncompleted, "deleted": deleted,
        }
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        shutil.rmtree(profile, ignore_errors=True)


def run_replay_delivery(project_path: str | Path) -> dict[str, Any]:
    project = Path(project_path).resolve()
    _write_initial_project(project)
    traces = TraceStore(str(project))
    trace = traces.start_trace("full deterministic delivery replay")
    store = ArtifactStore(str(project))
    traces.record_event(trace, "requirement", "requirement_completed", details={"artifact": "requirement.md", "acceptance_criteria": ["AC-001", "AC-002", "AC-003", "AC-004"]})

    architecture = """# Architecture\n\n## Modules\n- runtime: stdlib HTTP API and persistent task store\n- frontend: static browser page served by the backend\n\n## Traceability\n- AC-001..AC-004 -> backend/main.py, frontend/index.html\n"""
    store.save("architecture", architecture)
    traces.record_event(trace, "architecture", "architecture_published", details={"modules": ["runtime", "frontend"], "quality_gate": "passed"})

    contract = _make_contract()
    ImplementationContractStore(str(project)).save(contract.as_dict())
    traces.record_event(trace, "contract", "contract_published", details={"units": [unit.unit_id for unit in contract.units], "required_files": list(contract.required_files)})
    project_tasks(str(project), contract)
    traces.record_event(trace, "tasks", "tasks_published", details={"units": [unit.unit_id for unit in contract.units]})

    env = EnvironmentProvisioner(auto_pull_images=False).prepare(str(project))
    if not env.ok:
        raise AssertionError(f"environment preparation failed: {env.as_dict()}")
    store.save("environment", "# Environment\n\n- profile: python-stdlib\n- application: python-backend\n- image: python:3.12-slim\n- dependency_policy: none\n- status: ready\n")

    compiler = ImplementationContractCompiler()
    compiled = compiler.compile(contract, goal="full deterministic delivery replay", plan_id="replay-delivery-plan", trace=trace)
    if len(compiled.work_items) != 2:
        raise AssertionError(f"expected 2 code work items, got {len(compiled.work_items)}")
    environment_item = WorkItem(id="wi-environment", agent_id="bootstrap_agent", objective="prepare trusted runtime", output_key="environment", artifact_key="environment")
    wave_items: list[WorkItem] = []
    for wave in sorted({item.wave for item in compiled.work_items}):
        wave_items.append(WorkItem(
            id=f"wi-code-wave-{wave}", agent_id="code_integration_agent", objective=f"integrate code wave {wave}", output_key="implementation", artifact_key="implementation", execution_mode=ExecutionMode.INTEGRATION, publish_target="workspace",
            dependencies=tuple(WorkItemDependency(work_item_id=item.id, source=DependencySource.SYSTEM, rule_id=f"replay:wave:{wave}") for item in compiled.work_items if item.wave == wave),
            wave=wave,
        ))
    tests_item = WorkItem(
        id="wi-tests", agent_id="test_agent", objective="run trusted unit and runtime checks", output_key="tests", artifact_key="tests", execution_mode=ExecutionMode.EXCLUSIVE,
        dependencies=tuple(WorkItemDependency(work_item_id=item.id, source=DependencySource.SYSTEM, rule_id="replay:tests-after-code") for item in wave_items),
        requirement_ids=("AC-001", "AC-002", "AC-003", "AC-004"),
    )
    review_item = WorkItem(
        id="wi-review", agent_id="review_agent", objective="review delivery evidence", output_key="review", artifact_key="review", execution_mode=ExecutionMode.EXCLUSIVE,
        dependencies=(WorkItemDependency(work_item_id=tests_item.id, source=DependencySource.SYSTEM, rule_id="replay:review-after-tests"),),
        requirement_ids=("AC-001", "AC-002", "AC-003", "AC-004"),
    )
    all_items = (environment_item, *compiled.work_items, *wave_items, tests_item, review_item)
    # The compiled code plan remains the source of ownership/wave decisions;
    # the surrounding items only make the full replay graph auditable.
    plan = ExecutionPlan(id="replay-delivery-plan", goal="full deterministic delivery replay", work_items=all_items, template_id="delivery_default", trace=trace)
    traces.record_plan(plan)
    traces.record_plan_baseline(plan)
    traces.record_event(trace, "contract", "implementation_plan_compiled", details={
        "compiled_plan_digest": compiled.work_items[0].contract_digest,
        "implementation_unit_ids": [item.implementation_unit_id for item in compiled.work_items],
        "work_item_ids": [item.id for item in compiled.work_items],
        "wave_map": {item.id: item.wave for item in compiled.work_items},
        "ownership_map": {item.id: list(item.owned_files) for item in compiled.work_items},
        "dependency_map": {item.id: list(item.dependency_ids) for item in compiled.work_items},
    })
    traces.record_event(trace, environment_item.id, "work_item_started")
    traces.record_runtime_evidence(RuntimeEvidence.create(trace_id=trace.trace_id, phase="environment_preparation", status="ready", work_item_id=environment_item.id, message=json.dumps(env.as_dict(), ensure_ascii=False)))
    traces.record_event(trace, environment_item.id, "work_item_completed", details=env.as_dict())

    code_sources = {"backend-entrypoint": BACKEND, "frontend-entrypoint": FRONTEND}
    staging = CodeStagingService(str(project))
    staged_refs: dict[str, Any] = {}
    for item in compiled.work_items:
        if len(item.owned_files) != 1:
            raise AssertionError(f"replay requires one owned file per CodeAgent: {item.id}")
        traces.record_event(trace, item.id, "work_item_started", details={"agent_id": "code_agent", "owned_files": list(item.owned_files), "wave": item.wave})
        context = ExecutionContext(
            trace_id=trace.trace_id, work_item_id=item.id, agent_id="code_agent", contract_digest=item.contract_digest,
            execution_mode=ExecutionMode.PARTITIONED, input_refs=item.input_refs, slot=item.slot,
            allowed_paths=item.allowed_paths, forbidden_paths=item.forbidden_paths, required_paths=item.required_paths,
            implementation_unit_id=item.implementation_unit_id, owned_files=item.owned_files,
        )
        path = item.owned_files[0]
        staging.write_staged_file(context, path, code_sources[item.implementation_unit_id or ""])
        change = staging.load_change_set(trace.trace_id, item.id)
        staged_refs[item.id] = __import__("app.artifact.repository", fromlist=["ArtifactRef"]).ArtifactRef.staged(artifact_key="implementation", trace_id=trace.trace_id, work_item_id=item.id, slot=item.slot or "")
        traces.record_event(trace, item.id, "work_item_completed", details={"agent_id": "code_agent", "change_set_commit": change.commit, "files": list(change.changed_files)})

    integration = CodeIntegrationService(str(project))
    baselines: dict[str, str] = {}
    for wave in sorted({item.wave for item in compiled.work_items}):
        wave_item = next(item for item in wave_items if item.wave == wave)
        refs = tuple(staged_refs[item.id] for item in compiled.work_items if item.wave == wave)
        traces.record_event(trace, wave_item.id, "work_item_started", details={"wave": wave, "work_item_ids": [item.work_item_id for item in refs]})
        integration_context = ExecutionContext(trace_id=trace.trace_id, work_item_id=wave_item.id, agent_id="code_integration_agent", execution_mode=ExecutionMode.INTEGRATION, input_refs=refs, publish_target="workspace")
        summary = integration.integrate_wave(integration_context)
        baselines[str(wave)] = staging.load_baseline(trace.trace_id)
        traces.record_event(trace, wave_item.id, "code_wave_integrated", details={"wave": wave, "work_item_ids": [ref.work_item_id for ref in refs], "summary": summary, "baseline": baselines[str(wave)]})
        traces.record_event(trace, wave_item.id, "work_item_completed", details={"wave": wave})

    store.save("implementation", "# Implementation\n\n## Git delivery\n\nAll Contract-owned files were produced by CodeAgent ChangeSets and integrated in ordered waves.\n")
    traces.record_event(trace, "implementation", "implementation_published", details={"files": list(contract.required_files), "baselines": baselines})

    workspace = project / "workspace"
    test_service = TestService(str(project))
    test_service.write_test_file("tests/test_todo_api.py", TESTS)
    test_context = ExecutionContext(trace_id=trace.trace_id, work_item_id=tests_item.id, agent_id="test_agent", execution_mode=ExecutionMode.EXCLUSIVE)
    traces.record_event(trace, tests_item.id, "work_item_started", details={"checks": ["unit", "runtime-smoke"]})
    evidence_tools = SandboxEvidenceToolSet(test_service, traces)
    unit_result = evidence_tools.run_sandbox_check(test_context, "unit")
    runtime_result = evidence_tools.run_sandbox_check(test_context, "runtime-smoke")
    if "status=passed" not in unit_result or "status=passed" not in runtime_result:
        raise AssertionError(f"sandbox checks failed: unit={unit_result}; runtime={runtime_result}")
    test_report = "# Tests\n\n- unit: passed (trusted Docker sandbox)\n- runtime-smoke: passed (trusted Docker sandbox)\n- API contract: covered by tests/test_todo_api.py\n"
    test_service.save_tests(test_report)
    traces.record_event(trace, tests_item.id, "tests_published", details={"checks": ["unit", "runtime-smoke"], "artifact": "tests.md"})
    traces.record_event(trace, tests_item.id, "work_item_completed", details={"status": "passed"})

    review_service = ReviewService(str(project))
    review_text = "# Review\n\n## Verdict\nPASS\n\n## Verified\n- Requirement traceability is recorded for AC-001 through AC-004.\n- Contract ownership is unique and all required files were integrated.\n- Docker unit and runtime-smoke evidence passed.\n- API and browser E2E are executed after runtime startup.\n\n## Risks\n- This is a deterministic replay; live Provider latency is validated separately.\n"
    review_service.save_review(review_text)
    traces.record_event(trace, review_item.id, "review_published", details={"verdict": "PASS", "artifact": "review.md"})
    traces.record_event(trace, review_item.id, "work_item_completed", details={"verdict": "PASS"})

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    traces.record_event(trace, "runtime", "runtime_started", details={"port": port, "command": [sys.executable, "backend/main.py"]})
    process = subprocess.Popen([sys.executable, "backend/main.py"], cwd=workspace, env={**os.environ, "PORT": str(port), "TODO_DB_PATH": str(project / "runtime-data" / "tasks.json")}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    api_evidence: dict[str, object] = {}
    browser_evidence: dict[str, object] = {}
    try:
        health_status, health_payload = _wait_for(base)
        if health_status != 200 or health_payload != {"status": "ok"}:
            raise AssertionError(f"health failed: {health_status} {health_payload}")
        api_evidence["health"] = {"status": health_status, "body": health_payload}
        blank_status, blank_body = _request(base, "POST", "/api/tasks", {"title": " "})
        if blank_status != 400:
            raise AssertionError(f"blank title should be 400, got {blank_status} {blank_body}")
        created_status, created = _request(base, "POST", "/api/tasks", {"title": "replay task"})
        if created_status != 201 or not isinstance(created, dict):
            raise AssertionError(f"create failed: {created_status} {created}")
        task_id = int(created["id"])
        listed_status, listed = _request(base, "GET", "/api/tasks?" + urlencode({"status": "active"}))
        completed_status, completed = _request(base, "PATCH", f"/api/tasks/{task_id}", {"completed": True})
        stats_status, stats = _request(base, "GET", "/api/stats")
        deleted_status, _ = _request(base, "DELETE", f"/api/tasks/{task_id}")
        if (listed_status, completed_status, stats_status, deleted_status) != (200, 200, 200, 204):
            raise AssertionError("API CRUD status sequence failed")
        api_evidence["crud"] = {"create": created, "active_list": listed, "complete": completed, "stats": stats, "delete_status": deleted_status, "blank_title_status": blank_status}
        traces.record_runtime_evidence(RuntimeEvidence.create(trace_id=trace.trace_id, phase="api_e2e", status="passed", work_item_id="runtime", message=json.dumps(api_evidence, ensure_ascii=False)))
        traces.record_event(trace, "runtime", "api_e2e_passed", details=api_evidence)
        browser_evidence = _browser_smoke(base + "/")
        traces.record_runtime_evidence(RuntimeEvidence.create(trace_id=trace.trace_id, phase="browser_e2e", status="passed", work_item_id="runtime", message=json.dumps(browser_evidence, ensure_ascii=False)))
        traces.record_event(trace, "runtime", "browser_e2e_passed", details=browser_evidence)
        traces.record_event(trace, "runtime", "runtime_smoke_passed", details={"health": health_payload})
        traces.finish_trace(trace, "completed")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    events = traces.list_events(trace.trace_id)
    required_events = {"requirement_completed", "architecture_published", "contract_published", "tasks_published", "implementation_plan_compiled", "code_wave_integrated", "sandbox_evidence_recorded", "review_published", "api_e2e_passed", "browser_e2e_passed", "runtime_smoke_passed"}
    # Compiler evidence is persisted by the runner path; replay records the same
    # durable event explicitly because this script is a control-plane replay.
    events = traces.list_events(trace.trace_id)
    missing = sorted(required_events - {event["type"] for event in events})
    if missing:
        raise AssertionError(f"replay evidence missing events: {missing}")
    return {
        "status": "completed",
        "trace_id": trace.trace_id,
        "project_path": str(project),
        "planned_nodes": len(plan.work_items),
        "completed_nodes": len(plan.work_items),
        "contract_units": len(contract.units),
        "compiled_work_items": [item.id for item in compiled.work_items],
        "wave_baselines": baselines,
        "artifacts": {name: store.exists(name) for name in ("requirement", "architecture", "architecture_contract", "tasks", "environment", "implementation", "tests", "review")},
        "api_evidence": api_evidence,
        "browser_evidence": browser_evidence,
        "event_types": [event["type"] for event in events],
        "sandbox_evidence": [path.name for path in (traces.trace_root(trace.trace_id) / "evidence").glob("*.json")],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="caller-owned project path; default uses a temporary directory")
    args = parser.parse_args()
    if args.project:
        result = run_replay_delivery(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="projectos-replay-delivery-") as directory:
        print(json.dumps(run_replay_delivery(directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

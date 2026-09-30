"""Prepare and audit a versioned F-only correction of a checkpointed Live contract.

Never mutates project-contract.json, trace events, checkpoint, plan, or workspace.
The candidate requires explicit migration before it can become authoritative.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.architecture.implementation_contract import ImplementationContract
from app.domain.code.git_service import GitCodeIntegrationService
from app.domain.code.http_contract import validate_http_consumers
from app.orchestration.trace import TraceContext
from app.workflow.compiler import ImplementationContractCompiler
from app.workspace.git_repository import TaskBranch
from app.runtime.command_validator import CommandValidatorFactory

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "projects/live-l2-20260926-rerun/wanfa-live-l2-todo-20260927"
TRACE = "tr-2e4924e985cf"
OPERATIONS = [
    ("POST", "/tasks"), ("GET", "/tasks"),
    ("GET", "/tasks/{task_id}"),
    ("PATCH", "/tasks/{task_id}/status"),
    ("DELETE", "/tasks/{task_id}"),
    ("GET", "/tasks/statistics"),
]


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corrected(raw: dict) -> dict:
    amended = json.loads(json.dumps(raw))
    amended["entrypoints"].update({
        "backend_file": "backend/app/main.py",
        "backend_import": "app.main:app",
        "backend_command": "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
        "frontend_file": "frontend/index.html",
        "health_path": "/health",
    })
    for interface in amended["interfaces"]:
        if interface["interface_id"] == "task_management.task_api":
            interface["operations"] = [
                {"method": method, "path": path} for method, path in OPERATIONS
            ]
            interface["input_schema"] = (
                "POST /tasks: {title: string}; PATCH /tasks/{task_id}/status: "
                "{completed: boolean}; GET /tasks?status=active|completed 可选。"
            )
            interface["output_schema"] = (
                "Task={id: string, title: string, completed: boolean}; GET /tasks "
                "返回 Task[]; GET /tasks/statistics 返回 {total:int,active:int,completed:int}。"
            )
        elif interface["interface_id"] == "task_management.health":
            interface["operations"] = [{"method": "GET", "path": "/health"}]
    amended["interfaces"].extend([
        {"interface_id": "task_management.repository", "kind": "service",
         "name": "TaskRepository", "owner_unit": "task-management-repository",
         "owner_file": "backend/app/task_repository.py",
         "signature": "TaskRepository(database_path=None); create_task/get_task/list_tasks via SQLite"},
        {"interface_id": "task_management.service", "kind": "service",
         "name": "TaskService", "owner_unit": "task-management-service",
         "owner_file": "backend/app/task_service.py",
         "signature": "TaskService(repository=None,database_path=None); create_task/list_tasks"},
    ])
    units = {item["unit_id"]: item for item in amended["implementation_units"]}
    repo = units["task-management-repository"]
    repo["provides_interfaces"] = ["task_management.repository"]
    repo["provided_symbols"] = ["TaskRepository"]
    repo["constraints"].append(
        "运行数据库路径契约：优先读取 TASK_DB_PATH（绝对路径、持久卷）；未提供时 "
        "使用 backend/tasks.sqlite3。禁止仅在临时容器文件系统持久化。"
    )
    service = units["task-management-service"]
    service["provides_interfaces"] = ["task_management.service"]
    service["consumes_interfaces"] = ["task_management.repository"]
    service["provided_symbols"] = ["TaskService"]
    api = units["task-management-http"]
    api["consumes_interfaces"] = ["task_management.service"]
    api["provided_symbols"] = ["app"]
    api["constraints"].append(
        "正式浏览器路由仅使用冻结的 /tasks 系列 method/path；/api/tasks 与 "
        "/tasks/{task_id}/complete 等兼容别名不是合同接口，consumer 不得依赖。"
    )
    web_entry = units["task-web-entry"]
    web_entry["allowed_paths"].extend(["frontend/package.json", "frontend/vite.config.js"])
    web_entry["owned_files"].extend(["frontend/package.json", "frontend/vite.config.js"])
    web_entry["required_paths"].extend(["frontend/package.json", "frontend/vite.config.js"])
    web_entry["constraints"].append(
        "React/JSX 必须先用声明的 Vite/React 依赖执行 npm run build；浏览器不能"
        "由 python http.server 直接加载原始 JSX。开发服务器应代理同源 /tasks 至后端。"
    )
    runtime = units["mvp_delivery_docker_runtime"]
    runtime["constraints"].append(
        "容器后端以 workspace/backend 为工作目录，安装 FastAPI/Uvicorn 依赖后"
        "运行 python -m uvicorn app.main:app；TASK_DB_PATH 指向可写持久卷。"
        "浏览器入口只供应已构建的 frontend/dist，并将 /tasks 同源反向代理至后端；"
        "不得将 React JSX 当作静态 JS 直接提供。根目录 Compose 由运行配置显式对齐。"
    )
    return amended


def run(project: Path, output: Path) -> dict:
    original = project / ".projectos/architecture/project-contract.json"
    trace_root = project / ".projectos/runs" / TRACE
    trace_path = trace_root / "trace.json"
    plan_path = trace_root / "implementation-plan.json"
    checkpoint_path = trace_root / "checkpoint.json"
    originals = {str(path): _sha(path) for path in
                 (original, trace_path, plan_path, checkpoint_path)}
    raw = json.loads(original.read_text())
    plan = json.loads(plan_path.read_text())
    assert plan["contract_digest"] == _digest(raw), "original checkpointed contract digest differs"
    candidate = ImplementationContract.parse(corrected(raw))
    candidate_data = candidate.as_dict()
    candidate_digest = _digest(candidate_data)
    compiled = ImplementationContractCompiler().compile(
        candidate, goal="F-only contract correction dry run", plan_id="f-correction-dry-run",
        trace=TraceContext.ephemeral(),
    )
    items = {item.implementation_unit_id: item for item in compiled.work_items}
    bindings = {
        "service_requires_repository": items["task-management-service"].delivery_contract["required_bindings"],
        "http_requires_service": items["task-management-http"].delivery_contract["required_bindings"],
    }
    for key, module, symbol in (
        ("service_requires_repository", "app.task_repository", "TaskRepository"),
        ("http_requires_service", "app.task_service", "TaskService"),
    ):
        assert any(record["module"] == module and symbol in record["provided_symbols"]
                   for record in bindings[key]), (key, bindings[key])
    validator = CommandValidatorFactory.get_validator("python")
    result = validator.validate(candidate.entrypoints.backend_command)
    assert result.is_valid, result.reason
    http_issues = validate_http_consumers(project / "workspace", candidate)
    # The gate attributes every call in a consuming test file to each interface
    # that file consumes. GET /health is separately owned by the health interface,
    # not an undeclared task API route. Do not represent this as a clean gate pass.
    known_health_misattribution = (
        "tests/test_mvp_delivery.py:66 消费接口 task_management.task_api 使用 GET /health"
    )
    assert len(http_issues) == 1 and http_issues[0].startswith(known_health_misattribution), http_issues
    assert any(
        interface.interface_id == "task_management.health"
        and any(operation.method == "GET" and operation.path == "/health"
                for operation in interface.operations)
        for interface in candidate.interfaces
    ), "health must be explicitly owned by its own interface"
    # The JS consumer uses a helper and dynamic paths: the generic gate cannot
    # statically resolve it.  Check the explicit canonical source surface as
    # supporting evidence, not as proof of browser E2E.
    frontend_path = project / "workspace/frontend/src/App.jsx"
    frontend = frontend_path.read_text()
    patterns = {
        "base": r"const TASKS_PATH = '/tasks'",
        "list": r"request\(`\$\{TASKS_PATH\}\$\{query\}`\)",
        "create": r"request\(TASKS_PATH,\s*\{\s*method: 'POST'",
        "statistics": r"request\(`\$\{TASKS_PATH\}/statistics`\)",
        "status": r"request\(`\$\{TASKS_PATH\}/\$\{encodeURIComponent\(taskId\(task\)\)\}/status`,\s*\{\s*method: 'PATCH'",
        "delete": r"request\(`\$\{TASKS_PATH\}/\$\{encodeURIComponent\(taskId\(task\)\)\}`,\s*\{\s*method: 'DELETE'",
    }
    # Match the concrete consumer expression, not a guessed variable name.
    consumer_checks = {key: bool(re.search(expr, frontend)) for key, expr in patterns.items()}
    assert all(consumer_checks.values()), consumer_checks
    main_path = project / "workspace/backend/app/main.py"
    main_text = main_path.read_text()
    for method, path in [*OPERATIONS, ("GET", "/health")]:
        assert f'@app.{method.lower()}("{path}"' in main_text, (method, path)
    revision = project / ".projectos/architecture/revisions/project-contract-f-correction-v2-20260928.json"
    note = project / "architecture-f-correction-v2-20260928.md"
    if revision.exists() or note.exists() or output.exists():
        raise FileExistsError("F correction artifact already exists; refuse to overwrite")
    revision.parent.mkdir(parents=True, exist_ok=True)
    revision.write_text(json.dumps(candidate_data, ensure_ascii=False, indent=2) + "\n")
    note.write_text(
        "# F 类架构合同修订件（未激活）\n\n"
        f"- 原 Trace: `{TRACE}`，状态 `failed`；原合同 digest: `{plan['contract_digest']}`。\n"
        f"- 修订合同 digest: `{candidate_digest}`；9 个单元保留，冻结跨层接口及前端构建/运行约束。\n"
        "- 正式 API: POST/GET /tasks、GET /tasks/{task_id}、PATCH /tasks/{task_id}/status、"
        "DELETE /tasks/{task_id}、GET /tasks/statistics、GET /health。前端只消费 /tasks。\n"
        "- Python 绑定: app.task_repository:TaskRepository → app.task_service:TaskService "
        "→ app.main:app；后端工作目录 workspace/backend。\n"
        "- 启动命令: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`。\n"
        "- 前端：task-web-entry 拥有 package.json/vite.config.js，React/JSX 必须构建；"
        "浏览器同源 /tasks 须转发至后端。当前 Compose 的 http.server 无法做到。\n"
        "- SQLite: `TASK_DB_PATH` 应指向持久卷；缺省 backend/tasks.sqlite3。"
        "现有 repository 尚未读取该变量，Dockerfile/Compose 启动与数据库映射尚未对齐，"
        "属于未完成的 F 类实现修复，不能宣称容器持久化已通过。\n"
        "- 原 canonical project-contract.json、Trace、checkpoint、plan 和 workspace 均未修改。"
        "严禁直接覆盖 canonical 合同；迁移到原 Trace 须显式重规划并验证合同 digest/baseline。\n",
        encoding="utf-8",
    )
    for path, digest in originals.items():
        assert _sha(Path(path)) == digest, f"history mutated: {path}"
    evidence = {
        "status": "candidate_validated_not_activated", "classification": "F",
        "source_trace": TRACE, "source_trace_status": json.loads(trace_path.read_text())["status"],
        "canonical_contract_path": str(original), "canonical_contract_digest": plan["contract_digest"],
        "candidate_path": str(revision), "candidate_digest": candidate_digest,
        "revision_note": str(note), "full_units": len(candidate.units),
        "compiled_work_items": len(compiled.work_items), "bindings": bindings,
        "operations": [dict(method=m, path=p) for m,p in OPERATIONS],
        "static_checks": {"command_validator": result.is_valid,
                          "http_contract_gate": "one_known_cross_interface_misattribution",
                          "http_contract_gate_diagnostics": list(http_issues), "frontend_canonical_route_source": consumer_checks,
                          "frontend_source_sha256": _sha(frontend_path),
                          "frontend_build_manifest_present": (project / "workspace/frontend/package.json").exists(),
                          "frontend_vite_config_present": (project / "workspace/frontend/vite.config.js").exists(),
                          "provider_source_sha256": _sha(main_path)},
        "originals_sha256_unchanged": originals,
        "remaining_F_implementation_gaps": [
            "repository ignores TASK_DB_PATH", "Dockerfile backend import/cwd and dependency install are inconsistent",
            "docker-compose backend command and read-only database volume are inconsistent",
            "frontend has no package/Vite manifest; Compose serves raw JSX and does not proxy /tasks",
        ],
        "migration_needed": "explicit replan of persisted WorkItem contracts; no silent checkpoint rewrite",
        "command": ".venv/bin/python scripts/validate_live_f_contract_correction.py",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=LIVE)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "validation-evidence/f-design-correction-v2-20260928.json")
    args = parser.parse_args()
    evidence = run(args.project.resolve(), args.output.resolve())
    print(json.dumps({"status": evidence["status"], "candidate": evidence["candidate_path"],
                      "digest": evidence["candidate_digest"], "remaining": evidence["remaining_F_implementation_gaps"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

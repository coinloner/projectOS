from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.architecture.implementation_contract import ImplementationContractStore
from app.domain.code.git_service import GitCodeIntegrationService
from app.orchestration.trace import TraceContext
from app.workflow.compiler import ImplementationContractCompiler
from app.workspace.git_repository import TaskBranch


def _contract(*, main_source: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "interfaces": [
            {
                "interface_id": "task-api",
                "kind": "api",
                "name": "Task HTTP API",
                "owner_unit": "http",
                "owner_file": "backend/app/main.py",
            },
            {
                "interface_id": "task-service",
                "kind": "service",
                "name": "Task service",
                "owner_unit": "service",
                "owner_file": "backend/app/task_service.py",
            },
        ],
        "implementation_units": [
            {
                "unit_id": "service",
                "layer": "application",
                "objective": "provide service",
                "allowed_paths": ["backend/app/task_service.py"],
                "owned_files": ["backend/app/task_service.py"],
                "provided_symbols": ["TaskService"],
                "provides_interfaces": ["task-service"],
            },
            {
                "unit_id": "http",
                "layer": "api",
                "objective": "provide http api",
                "allowed_paths": ["backend/app/main.py"],
                "owned_files": ["backend/app/main.py"],
                "provided_symbols": ["app"],
                "required_symbols": ["app"],
                "depends_on": ["service"],
                "provides_interfaces": ["task-api"],
            },
        ],
    }
    if main_source is not None:
        payload["_main_source"] = main_source
    return payload


def _branch(root: Path) -> TaskBranch:
    return TaskBranch("trace", "integration", "branch", "base", str(root))


def test_integration_rejects_dynamic_local_module_fallback() -> None:
    with TemporaryDirectory() as directory:
        project = Path(directory)
        ImplementationContractStore(str(project)).save(_contract())
        workspace = project / "integration" / "workspace" / "backend" / "app"
        workspace.mkdir(parents=True)
        (workspace / "task_service.py").write_text(
            "class TaskService:\n    pass\n", encoding="utf-8"
        )
        (workspace / "main.py").write_text(
            """
import importlib
from fastapi import FastAPI
app = FastAPI()
_SERVICE_MODULES = ("app.task_management.service", "app.task_service")
def get_service():
    for name in _SERVICE_MODULES:
        return importlib.import_module(name)
""",
            encoding="utf-8",
        )

        issues = GitCodeIntegrationService(str(project))._validate_contract_bindings(
            _branch(project / "integration")
        )

        assert any("动态本地模块回退" in issue for issue in issues)
        assert any("backend/app/main.py" in issue for issue in issues)


def test_integration_rejects_symbol_not_exported_by_canonical_provider() -> None:
    with TemporaryDirectory() as directory:
        project = Path(directory)
        ImplementationContractStore(str(project)).save(_contract())
        workspace = project / "integration" / "workspace" / "backend" / "app"
        workspace.mkdir(parents=True)
        (workspace / "task_service.py").write_text(
            "class TaskService:\n    pass\n", encoding="utf-8"
        )
        (workspace / "main.py").write_text(
            "from app.task_service import TaskManagementService\n", encoding="utf-8"
        )

        issues = GitCodeIntegrationService(str(project))._validate_contract_bindings(
            _branch(project / "integration")
        )

        assert any("未声明符号" in issue for issue in issues)
        assert any("provider 不存在的符号" in issue for issue in issues)


def test_integration_allows_canonical_import_and_external_dynamic_import() -> None:
    with TemporaryDirectory() as directory:
        project = Path(directory)
        ImplementationContractStore(str(project)).save(_contract())
        workspace = project / "integration" / "workspace" / "backend" / "app"
        workspace.mkdir(parents=True)
        (workspace / "task_service.py").write_text(
            "class TaskService:\n    pass\n", encoding="utf-8"
        )
        (workspace / "main.py").write_text(
            """
import importlib
from app.task_service import TaskService
service_type = TaskService
json_module = importlib.import_module("json")
""",
            encoding="utf-8",
        )

        issues = GitCodeIntegrationService(str(project))._validate_contract_bindings(
            _branch(project / "integration")
        )

        assert issues == ()


def test_integration_rejects_missing_methods_on_direct_bound_instance() -> None:
    with TemporaryDirectory() as directory:
        project = Path(directory)
        ImplementationContractStore(str(project)).save(_contract())
        workspace = project / "integration/workspace/backend/app"
        workspace.mkdir(parents=True)
        (workspace / "task_service.py").write_text(
            "class TaskService:\n    def create_task(self, title): return title\n",
            encoding="utf-8",
        )
        (workspace / "main.py").write_text(
            "from app.task_service import TaskService as Service\n"
            "service = Service()\n"
            "def stats(): return service.get_stats()\n",
            encoding="utf-8",
        )
        issues = GitCodeIntegrationService(str(project))._validate_contract_bindings(
            _branch(project / "integration")
        )
        assert any("backend/app/main.py" in issue and "get_stats" in issue for issue in issues)
        assert not any("task_service.py 调用了" in issue for issue in issues)


def test_integration_allows_direct_bound_class_method() -> None:
    with TemporaryDirectory() as directory:
        project = Path(directory)
        ImplementationContractStore(str(project)).save(_contract())
        workspace = project / "integration/workspace/backend/app"
        workspace.mkdir(parents=True)
        (workspace / "task_service.py").write_text(
            "class TaskService:\n    def get_statistics(self): return {}\n",
            encoding="utf-8",
        )
        (workspace / "main.py").write_text(
            "from app.task_service import TaskService\n"
            "service = TaskService()\n"
            "def stats(): return service.get_statistics()\n",
            encoding="utf-8",
        )
        assert GitCodeIntegrationService(str(project))._validate_contract_bindings(
            _branch(project / "integration")
        ) == ()


def test_compiler_maps_in_process_dependency_but_not_http_interface_to_python_import() -> None:
    payload = _contract()
    payload["implementation_units"] = [
        {
            "unit_id": "service",
            "layer": "application",
            "objective": "provide service",
            "allowed_paths": ["backend/app/task_service.py"],
            "owned_files": ["backend/app/task_service.py"],
            "provided_symbols": ["TaskService"],
            "provides_interfaces": ["task-service"],
        },
        {
            "unit_id": "consumer",
            "layer": "api",
            "objective": "consume service and api",
            "allowed_paths": ["backend/app/consumer.py"],
            "owned_files": ["backend/app/consumer.py"],
            "depends_on": ["service"],
            "consumes_interfaces": ["task-service", "task-api"],
        },
    ]
    payload["interfaces"] = [
        {
            "interface_id": "task-api",
            "kind": "api",
            "name": "Task HTTP API",
            "owner_unit": "service",
        },
        {
            "interface_id": "task-service",
            "kind": "service",
            "name": "Task service",
            "owner_unit": "service",
        },
    ]

    from app.domain.architecture.implementation_contract import ImplementationContract

    plan = ImplementationContractCompiler().compile(
        ImplementationContract.parse(payload),
        goal="bindings",
        plan_id="bindings-plan",
        trace=TraceContext(requirement_id="req", trace_id="trace"),
    )
    contract = plan.work_items[1].delivery_contract or {}
    required = contract["required_bindings"]

    assert [item["module"] for item in required] == ["app.task_service"]
    assert {item["consumption"] for item in required} == {"import_code"}
    assert all(item.get("consumes_interface") != "task-api" for item in required)

from app.domain.architecture.implementation_contract import ImplementationContract
from app.domain.task.projection import render_tasks


def test_render_tasks_is_deterministic_contract_projection():
    contract = ImplementationContract.parse({
        "schema_version": 1,
        "compilation_strategy": "semantic",
        "implementation_units": [{
            "unit_id": "tasks-capability",
            "layer": "capability",
            "objective": "实现任务能力",
            "allowed_paths": ["backend/tasks/**"],
            "owned_files": ["backend/tasks/service.py", "backend/tasks/routes.py"],
            "acceptance_criteria": ["可创建任务", "空标题被拒绝"],
        }],
    })
    first = render_tasks(contract)
    second = render_tasks(contract)
    assert first == second
    assert "tasks-capability" in first
    assert "backend/tasks/service.py, backend/tasks/routes.py" in first
    assert "空标题被拒绝" in first

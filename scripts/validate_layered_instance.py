"""Run a deterministic real-project validation of the architecture_layered route."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Keep the validation script runnable both as ``python -m`` and directly from
# the repository root.  Python otherwise places only ``scripts/`` on
# sys.path, which hides the sibling ``app`` package.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.artifact.repository import ArtifactRepository
from app.artifact.store import ArtifactStore
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    LayerDecision,
    ModuleDesign,
    ModuleRef,
    ImplementationDesign,
)
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.orchestration.trace import TraceStore
from app.tool_manager.gateway import ToolGateway
from app.bootstrap.runtime import build_container


def _unit(module_id: str) -> dict[str, object]:
    return {
        "unit_id": f"unit-{module_id}",
        "layer": module_id,
        "objective": f"实现{module_id}模块的最小可运行文件",
        "allowed_paths": [f"backend/app/{module_id}/**"],
        "required_files": [f"backend/app/{module_id}/main.py"],
        "owned_files": [f"backend/app/{module_id}/main.py"],
        "acceptance_criteria": ["文件可解析并符合模块边界"],
    }


class DeterministicArchitectureAgent:
    def __init__(self, project_path: str) -> None:
        self.workflow = ArchitectureArtifactWorkflow(project_path)

    def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
        assert context is not None
        slot = context.slot
        if context.execution_mode is ExecutionMode.PARTITIONED:
            if slot == "blueprint":
                value = ArchitectureBlueprint(
                    schema_version=1,
                    design_id="blueprint-orders",
                    system_boundary="订单与库存服务负责创建订单、查询库存并提供健康检查。",
                    layers=[
                        LayerDecision(name="domain", path_mapping=["backend/app/domain/**"]),
                        LayerDecision(name="api", allowed_dependencies=["domain"], path_mapping=["backend/app/api/**"]),
                        LayerDecision(name="runtime", allowed_dependencies=["api", "domain"], path_mapping=["backend/app/runtime/**"]),
                    ],
                    modules=[
                        ModuleRef(module_id="domain", responsibility="订单和库存业务规则"),
                        ModuleRef(module_id="api", responsibility="HTTP 接口与输入输出适配"),
                        ModuleRef(module_id="runtime", responsibility="应用启动和运行时配置"),
                    ],
                    global_constraints=["API 不得直接访问数据库实现细节"],
                    required_files=["backend/app/domain/main.py", "backend/app/api/main.py", "backend/app/runtime/main.py"],
                    requirement_ids=["AC-001", "AC-002"],
                )
            elif slot and slot.startswith("module-"):
                module_id = slot.removeprefix("module-")
                value = ModuleDesign(
                    schema_version=1,
                    design_id=f"module-design-{module_id}",
                    parent_design_id="blueprint-orders",
                    module_id=module_id,
                    responsibilities=[f"{module_id} 模块职责"],
                    requirement_ids=["AC-001", "AC-002"],
                )
            elif slot and slot.startswith("implementation-"):
                module_id = slot.removeprefix("implementation-").removeprefix("module-")
                value = ImplementationDesign(
                    schema_version=1,
                    design_id=f"implementation-design-{module_id}",
                    parent_design_id=f"module-design-{module_id}",
                    module_id=module_id,
                    implementation_units=[_unit(module_id)],
                    required_test_types=["domain_unit", "api_http"],
                    requirement_ids=["AC-001", "AC-002"],
                )
            else:
                raise AssertionError(f"unexpected partition slot: {slot}")
            return AgentResult.completed(self.workflow.write_staged_design(context, value.model_dump(mode="json")))

        if context.execution_mode is ExecutionMode.INTEGRATION:
            if context.agent_id == "architecture_agent":
                return AgentResult.completed(self.workflow.integrate_structured_designs(context))
            return AgentResult.completed(self.workflow.compile_project_contract_from_designs(context))

        raise AssertionError(f"unexpected execution mode: {context.execution_mode}")


class StaticPlannerRuntime:
    """Select the new route deterministically without requiring a provider call."""

    def generate(self, prompt: str) -> str:
        return json.dumps(
            {
                "rationale": "订单与库存服务需要分层架构合同。",
                "template_hint_id": "architecture_layered",
                "steps": [],
            },
            ensure_ascii=False,
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="projectos-layered-") as directory:
        project = Path(directory)
        ArtifactStore(str(project)).save(
            "requirement",
            "# 订单与库存服务\n\n## 验收标准\n- AC-001 创建订单\n- AC-002 查询库存\n",
        )
        container = build_container(str(project), planner_runtime=StaticPlannerRuntime())
        planned = container.planner.plan(
            goal="构建订单与库存服务", plan_id="plan-layered-orders"
        )
        plan = planned.plan
        trace = plan.trace
        traces = container.traces
        agents = AgentRegistry()
        agents.register(
            AgentDefinition("architecture_agent", "architecture", "分层架构设计", "architecture", max_parallel_instances=3),
            lambda: DeterministicArchitectureAgent(str(project)),
        )
        agents.register(
            AgentDefinition("architecture_contract_agent", "architecture_contract", "合同编译", "architecture_contract"),
            lambda: DeterministicArchitectureAgent(str(project)),
        )
        runner = GraphRunner(
            agents,
            ToolGateway(),
            traces=traces,
            artifacts=ArtifactRepository(str(project)),
            max_workers=3,
        )
        result = runner.run(plan)
        contract_path = project / ".projectos" / "architecture" / "project-contract.json"
        summary = {
            "status": result.status.value,
            "completed_nodes": len(result.state.node_results),
            "planned_nodes": len(result.state.plan.work_items),
            "architecture_published": ArtifactStore(str(project)).exists("architecture"),
            "contract_published": contract_path.is_file(),
            "contract_units": len(json.loads(contract_path.read_text(encoding="utf-8")).get("implementation_units", [])) if contract_path.is_file() else 0,
            "trace_id": trace.trace_id,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if result.status is not GraphRunStatus.COMPLETED:
            raise SystemExit(f"layered route failed: {result.error or result.status.value}")


if __name__ == "__main__":
    main()

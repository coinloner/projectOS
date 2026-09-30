"""Run a deterministic real-project validation of the architecture_layered route."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Keep the validation script runnable both as ``python -m`` and directly from
# the repository root.  Python otherwise places only ``scripts/`` on
# sys.path, which hides the sibling ``app`` package.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.artifact.store import ArtifactStore
from app.domain.architecture.design_contract import (
    ArchitectureBlueprint,
    LayerDecision,
    ModuleDesign,
    ModuleRef,
    ImplementationDesign,
)
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.domain.architecture.implementation_contract import ImplementationContractStore
from app.workflow.compiler import ImplementationContractCompiler
from app.execution_context import ExecutionContext, ExecutionMode
from app.orchestration.runner import GraphRunner, GraphRunStatus
from app.orchestration.trace import TraceStore
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
                        ModuleRef(
                            module_id="domain",
                            responsibility="订单和库存业务规则",
                            owned_required_files=["backend/app/domain/main.py"],
                        ),
                        ModuleRef(
                            module_id="api",
                            responsibility="HTTP 接口与输入输出适配",
                            depends_on_modules=["domain"],
                            owned_required_files=["backend/app/api/main.py"],
                        ),
                        ModuleRef(
                            module_id="runtime",
                            responsibility="应用启动和运行时配置",
                            depends_on_modules=["api", "domain"],
                            owned_required_files=["backend/app/runtime/main.py"],
                        ),
                    ],
                    global_constraints=["API 不得直接访问数据库实现细节"],
                    required_files=["backend/app/domain/main.py", "backend/app/api/main.py", "backend/app/runtime/main.py"],
                    requirement_ids=["AC-001", "AC-002"],
                )
            elif slot and slot.startswith("module-"):
                module_id = slot.removeprefix("module-")
                dependencies = {
                    "domain": [],
                    "api": ["domain"],
                    "runtime": ["api", "domain"],
                }[module_id]
                value = ModuleDesign(
                    schema_version=1,
                    design_id=f"module-design-{module_id}",
                    parent_design_id="blueprint-orders",
                    module_id=module_id,
                    purpose={
                        "domain": "订单和库存业务规则",
                        "api": "HTTP 接口与输入输出适配",
                        "runtime": "应用启动和运行时配置",
                    }[module_id],
                    responsibilities=[f"{module_id} 模块职责"],
                    depends_on_modules=dependencies,
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


class AgentResultAgent:
    def __init__(self, content: str) -> None:
        self.content = content

    def run(self, task: str, *, context: ExecutionContext | None = None) -> AgentResult:
        return AgentResult.completed(self.content)


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


def run_layered_validation(project_path: str | Path) -> dict[str, Any]:
    """Run the L0 semantic loop in a caller-owned project directory."""
    project = Path(project_path).resolve()
    project.mkdir(parents=True, exist_ok=True)
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

    # Reuse the bootstrapped ToolGateway (all domain tools are installed),
    # but replace the agent registry with deterministic replay agents.  A bare
    # GraphRunner/ToolGateway pair bypasses module installation and fails
    # preflight before the validation route can execute.
    replay_agents = AgentRegistry()
    replay_agents.register(
        AgentDefinition("requirement_agent", "requirement", "预置需求", "requirement"),
        lambda: AgentResultAgent("需求已预置"),
    )
    replay_agents.register(
        AgentDefinition(
            "architecture_agent",
            "architecture",
            "分层架构设计",
            "architecture",
            max_parallel_instances=3,
        ),
        lambda: DeterministicArchitectureAgent(str(project)),
    )
    replay_agents.register(
        AgentDefinition(
            "architecture_contract_agent",
            "architecture_contract",
            "合同编译",
            "architecture_contract",
        ),
        lambda: DeterministicArchitectureAgent(str(project)),
    )
    replay_runner = GraphRunner(
        replay_agents,
        container.gateway,
        max_workers=3,
        traces=traces,
        artifacts=container.artifact_repository,
        skills=container.skills,
        policies=container.policies,
        architecture_config=container.runner._architecture_config,
    )
    result = replay_runner.run(plan)
    contract_path = project / ".projectos" / "architecture" / "project-contract.json"
    compiled_work_items: list[str] = []
    contract_digest = None
    if contract_path.is_file():
        contract = ImplementationContractStore(str(project)).load()
        compiled = ImplementationContractCompiler().compile(
            contract,
            goal=plan.goal,
            plan_id=plan.id,
            trace=trace,
        )
        compiled_work_items = [item.id for item in compiled.work_items]
        contract_digest = __import__("hashlib").sha256(
            json.dumps(contract.as_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    event_types = [event["type"] for event in traces.list_events(trace.trace_id)]
    summary = {
        "status": result.status.value,
        "completed_nodes": len(result.state.node_results),
        "planned_nodes": len(result.state.plan.work_items),
        "architecture_published": ArtifactStore(str(project)).exists("architecture"),
        "contract_published": contract_path.is_file(),
        "contract_units": (
            len(json.loads(contract_path.read_text(encoding="utf-8")).get("implementation_units", []))
            if contract_path.is_file()
            else 0
        ),
        "compiled_work_items": compiled_work_items,
        "contract_digest": contract_digest,
        "semantic_loop_completed": "semantic_loop_completed" in event_types,
        "trace_id": trace.trace_id,
    }
    if result.status is not GraphRunStatus.COMPLETED:
        raise AssertionError(f"layered route failed: {result.error or result.status.value}")
    if not summary["semantic_loop_completed"]:
        raise AssertionError("L0 缺少 semantic_loop_completed 控制面事件")
    if not compiled_work_items:
        raise AssertionError("L0 Contract Compiler 未生成任何 WorkItem")
    return summary


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="projectos-layered-") as directory:
        print(json.dumps(run_layered_validation(directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

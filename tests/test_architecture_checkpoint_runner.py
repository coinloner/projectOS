"""Matched deterministic fault: compare repeated work, not provider success rates."""
import json
from dataclasses import replace

import pytest
from app.architecture_execution_config import ArchitectureExecutionConfig

from app.agent.registry import AgentDefinition, AgentRegistry
from app.agent.result import AgentResult
from app.artifact.repository import ArtifactRepository
from app.domain.architecture.design_contract import ModuleDesign, ModuleRef
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.domain.architecture.tools import register_architecture_tools
from app.orchestration.runner import GraphRunner
from app.orchestration.trace import TraceStore
from app.tool_manager.gateway import ToolGateway
from tests.test_dynamic_builder import _base_plan, _blueprint


@pytest.mark.parametrize("checkpoints,expected_boundary_executions", [(False, 2), (True, 1)])
def test_runner_retry_reuses_only_explicitly_enabled_checkpoint(tmp_path, checkpoints, expected_boundary_executions):
    project_path = str(tmp_path)
    blueprint = _blueprint(ModuleRef(module_id="catalog", responsibility="目录", purpose="发现商品"))
    tools = ToolGateway()
    register_architecture_tools(tools, project_path)
    calls = {"module_attempts": 0, "boundaries": 0, "interfaces": 0}

    class Agent:
        def run(self, task, *, context=None):
            workflow = ArchitectureArtifactWorkflow(project_path)
            visible = {tool.name: tool for tool in tools.tools_for("architecture", context=context)}
            if context.slot == "blueprint":
                assert "load_architecture_intermediate" not in visible
                workflow.write_staged_design(context, blueprint.model_dump(mode="json"))
                return AgentResult.completed("saved")
            calls["module_attempts"] += 1
            assert ("load_architecture_intermediate" in visible) == checkpoints
            phase = None
            if checkpoints:
                phase = json.loads(visible["load_architecture_intermediate"].run(
                    phases=["boundaries", "interfaces"]))["phase"]
            if phase is None:
                calls["boundaries"] += 1
                if checkpoints:
                    visible["write_architecture_intermediate"].run(
                        phase="boundaries", content="catalog boundary")
            if calls["module_attempts"] == 1:
                raise RuntimeError("connection reset: deterministic post-boundary fault")
            calls["interfaces"] += 1
            design = ModuleDesign(schema_version=1, design_id="module-catalog",
                parent_design_id=blueprint.design_id, module_id="catalog",
                purpose="发现商品", responsibilities=["catalog"])
            visible["write_module_design"].run(design=design.model_dump(mode="json"))
            return AgentResult.completed("saved")

    traces = TraceStore(project_path)
    original = _base_plan(False)
    plan = replace(original, trace=traces.start_trace("matched retry"),
        work_items=(replace(original.work_items[0], input_refs=(), contract_digest=None),))
    agents = AgentRegistry()
    agents.register(AgentDefinition("architecture_agent", "architecture", "architecture", "architecture"), Agent)
    result = GraphRunner(agents, tools, traces=traces, artifacts=ArtifactRepository(project_path),
        max_workers=1, architecture_config=ArchitectureExecutionConfig(mode="checkpointed" if checkpoints else "baseline")).run(plan)
    assert result.status.value == "completed", result.error
    assert calls == {"module_attempts": 2, "boundaries": expected_boundary_executions, "interfaces": 1}
    assert "wi-architecture-module-catalog" in result.state.node_results
    config = ArchitectureExecutionConfig("checkpointed" if checkpoints else "baseline")
    events = [event for event in traces.list_events(plan.trace.trace_id)
              if event["type"] == "architecture_execution_config"]
    assert events
    assert all(event["details"] == {"config": config.as_dict(), "digest": config.digest}
               for event in events)

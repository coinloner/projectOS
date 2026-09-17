from dataclasses import replace
from app.architecture_execution_config import ArchitectureExecutionConfig

from app.agent.architecture_agent import ArchitectureAgent
from app.execution_context import ExecutionContext, ExecutionMode


def test_checkpoint_instructions_require_explicit_partition_grant():
    agent = ArchitectureAgent(None)
    context = ExecutionContext(trace_id="isolation", work_item_id="module-api",
        agent_id="architecture_agent", execution_mode=ExecutionMode.PARTITIONED,
        slot="module-api", tool_allowlist=("load_architecture_input", "write_module_design"))
    assert "load_architecture_intermediate" not in agent.backstory_for_context(context)
    assert "load_architecture_intermediate" not in agent.backstory_for_context(None)
    for tool in ("load_architecture_intermediate", "write_architecture_intermediate"):
        assert "节点内恢复规则" not in agent.backstory_for_context(
            replace(context, tool_allowlist=context.tool_allowlist + (tool,)))
    b_context = replace(context, architecture_config=ArchitectureExecutionConfig("checkpointed"), tool_allowlist=context.tool_allowlist + (
        "load_architecture_intermediate", "write_architecture_intermediate"))
    assert "节点内恢复规则" in agent.backstory_for_context(b_context)
    assert "节点内恢复规则" not in agent.backstory_for_context(
        replace(b_context, execution_mode=ExecutionMode.EXCLUSIVE))
    # B execution must not mutate the next A attempt's policy.
    assert agent.backstory_for_context(context) == agent.backstory_for_context(None)

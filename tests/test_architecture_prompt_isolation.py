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
    partitioned = agent.backstory_for_context(context)
    exclusive = agent.backstory_for_context(replace(context, execution_mode=ExecutionMode.EXCLUSIVE))
    # Partitioned delivery has a deliberately narrower contract than the
    # global/exclusive prompt: it must not require invisible selection tools.
    assert "必须先调用 select_tech_stack" not in partitioned
    assert "必须先调用 select_interface_kind" not in partitioned
    assert "不要把它们报告为缺失的外部能力" in partitioned
    assert "system_boundary、layers、modules" in partitioned
    assert "不要给 layer 写" in partitioned
    assert "select_tech_stack" in exclusive
    assert "select_interface_kind" in exclusive
    # A checkpointed B attempt must not mutate the next A attempt's policy.
    assert agent.backstory_for_context(context) == partitioned


def test_architecture_writer_has_room_for_read_and_schema_repair_turns():
    # CrewAI counts each LLM/tool iteration. A progress + input read used up
    # the previous 3-turn limit before the mandatory writer could be retried.
    assert ArchitectureAgent(None)._max_iterations == 6

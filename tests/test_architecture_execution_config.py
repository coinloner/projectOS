from dataclasses import FrozenInstanceError, replace

import pytest

from app.agent.architecture_agent import ArchitectureAgent
from app.architecture_execution_config import ArchitectureExecutionConfig
from app.execution_context import ExecutionContext, ExecutionMode


def test_config_is_immutable_roundtrippable_and_identifiable():
    a = ArchitectureExecutionConfig()
    b = ArchitectureExecutionConfig("checkpointed")
    assert a.digest != b.digest
    assert ArchitectureExecutionConfig(**b.as_dict()) == b
    assert ArchitectureExecutionConfig(**b.as_dict()).digest == b.digest
    with pytest.raises(FrozenInstanceError):
        b.mode = "baseline"
    with pytest.raises(ValueError):
        ArchitectureExecutionConfig("revision_aware")
    with pytest.raises(ValueError):
        ArchitectureExecutionConfig(schema_version=2)


def test_tools_alone_cannot_enable_baseline_checkpoint_prompt():
    agent = ArchitectureAgent(None)
    b = ArchitectureExecutionConfig("checkpointed")
    ctx = ExecutionContext(trace_id="t", work_item_id="w", agent_id="architecture_agent",
        execution_mode=ExecutionMode.PARTITIONED, slot="module-api",
        tool_allowlist=b.intermediate_tools)
    baseline = agent.backstory_for_context(ctx)
    assert "节点内恢复规则" not in baseline
    assert "节点内恢复规则" in agent.backstory_for_context(replace(ctx, architecture_config=b))
    assert agent.backstory_for_context(ctx) == baseline
    assert "节点内恢复规则" not in agent.backstory_for_context(
        replace(ctx, architecture_config=b, slot="blueprint"))


@pytest.mark.parametrize("agent,mode,slot", [
    ("code_agent", "partitioned", "module-api"),
    ("architecture_agent", "integration", "module-api"),
    ("architecture_agent", "partitioned", "blueprint"),
])
def test_checkpoint_policy_does_not_expand_other_node_permissions(agent, mode, slot):
    assert not ArchitectureExecutionConfig("checkpointed").supports_checkpoints(agent, mode, slot)

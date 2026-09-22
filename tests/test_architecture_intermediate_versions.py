"""Recovery must bind to real input identities, bytes and execution contract."""
from dataclasses import replace
from hashlib import sha256

import pytest

from app.artifact.repository import ArtifactRef
from app.artifact.store import ArtifactStore
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.execution_context import ExecutionContext, ExecutionMode


@pytest.fixture
def checkpoint(tmp_path):
    store = ArtifactStore(str(tmp_path))
    store.save("requirement", "original")
    workflow = ArchitectureArtifactWorkflow(str(tmp_path))
    context = ExecutionContext(
        trace_id="version-test", work_item_id="module-api", agent_id="architecture_agent",
        execution_mode=ExecutionMode.PARTITIONED, slot="module-api",
        input_refs=(ArtifactRef.published("requirement"),),
        input_digests=(sha256(b"original").hexdigest(),), contract_digest="a" * 64,
    )
    workflow.write_intermediate(context, "boundaries", "confirmed boundary")
    return workflow, context, store


@pytest.mark.parametrize("operation", ["load", "write"])
@pytest.mark.parametrize("digests,exception", [(None, ValueError), ((), RuntimeError), (("0" * 64,), RuntimeError)])
def test_reject_missing_or_false_input_evidence(checkpoint, operation, digests, exception):
    workflow, context, _ = checkpoint
    context = replace(context, input_digests=digests)
    with pytest.raises(exception):
        if operation == "load":
            workflow.load_intermediate(context, ["boundaries"])
        else:
            workflow.write_intermediate(context, "interfaces", "not valid")


@pytest.mark.parametrize("operation", ["load", "write"])
def test_reject_input_changed_during_attempt(checkpoint, operation):
    workflow, context, store = checkpoint
    store.save("requirement", "changed")
    with pytest.raises(RuntimeError, match="输入已改变"):
        if operation == "load":
            workflow.load_intermediate(context, ["boundaries"])
        else:
            workflow.write_intermediate(context, "interfaces", "stale output")


@pytest.mark.parametrize("change", ["contract", "slot", "reference", "version"])
def test_reject_checkpoint_from_other_contract_or_input(checkpoint, change):
    workflow, context, store = checkpoint
    if change == "contract":
        context = replace(context, contract_digest="b" * 64)
    elif change == "slot":
        context = replace(context, slot="module-other")
    elif change == "reference":
        store.save("architecture", "original")  # Identical bytes, different authority.
        context = replace(context, input_refs=(ArtifactRef.published("architecture"),))
    else:
        store.save("requirement", "changed")
        context = replace(context, input_digests=(sha256(b"changed").hexdigest(),))
    with pytest.raises(RuntimeError, match="中间产物输入版本或内容摘要不匹配"):
        workflow.load_intermediate(context, ["boundaries"])


def test_fresh_service_can_resume_same_contract(checkpoint):
    import json
    workflow, context, store = checkpoint
    # Create an independent service, not an in-memory reuse of the writer.
    fresh = ArchitectureArtifactWorkflow(str(store.project_path))
    assert json.loads(fresh.load_intermediate(context, ["boundaries", "interfaces"])) == {
        "phase": "boundaries", "content": "confirmed boundary",
    }


def test_checkpoint_rejects_changed_architecture_policy(checkpoint):
    from app.architecture_execution_config import ArchitectureExecutionConfig
    workflow, context, _ = checkpoint
    with pytest.raises(RuntimeError, match="中间产物输入版本或内容摘要不匹配"):
        workflow.load_intermediate(replace(context,
            architecture_config=ArchitectureExecutionConfig("checkpointed")), ["boundaries"])

from app.execution_context import ExecutionMode
from app.workflow.templates import delivery_default_template
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.execution_context import ExecutionContext
from app.architecture_execution_config import ArchitectureExecutionConfig


def test_contract_compiler_rejects_unversioned_design_inputs(tmp_path):
    workflow = ArchitectureArtifactWorkflow(str(tmp_path))
    context = ExecutionContext(
        trace_id="tr-contract-evidence",
        work_item_id="wi-05-contract",
        agent_id="architecture_contract_agent",
        execution_mode=ExecutionMode.INTEGRATION,
        publish_target="architecture_contract",
        input_refs=(),
        input_digests=None,
        architecture_config=ArchitectureExecutionConfig(scheme="D"),
    )
    # An empty set is valid input evidence, but a non-empty set without
    # matching digests must fail with a control-plane diagnostic rather than an
    # IndexError.  Populate one ref by constructing a real staged artifact.
    ref = workflow._repository.write_staged(
        trace_id=context.trace_id,
        work_item_id="producer",
        artifact_key="architecture",
        slot="blueprint",
        content="{}",
    ).ref
    invalid = ExecutionContext(
        trace_id=context.trace_id,
        work_item_id=context.work_item_id,
        agent_id=context.agent_id,
        execution_mode=context.execution_mode,
        publish_target=context.publish_target,
        input_refs=(ref,),
        input_digests=None,
        architecture_config=context.architecture_config,
    )
    try:
        workflow.validate_design_inputs(invalid)
    except ValueError as error:
        assert "input_refs=1" in str(error)
        assert "input_digests=0" in str(error)
    else:
        raise AssertionError("unversioned contract input was accepted")


def test_default_delivery_contract_node_is_integration_and_has_compiler_boundary():
    node = next(item for item in delivery_default_template().nodes if item.stage_id == "contract")
    contract = node
    assert contract.agent_id == "architecture_contract_agent"
    assert contract.execution_mode is ExecutionMode.INTEGRATION
    assert contract.publish_target == "architecture_contract"

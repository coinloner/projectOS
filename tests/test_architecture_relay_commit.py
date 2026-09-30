"""Exercise the real architecture tool boundary when a relay emits textual calls."""
import json
from unittest.mock import patch


from app.agent.base_agent import BaseAgent
from app.agent.result import AgentStatus
from app.architecture_execution_config import ArchitectureExecutionConfig
from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.domain.architecture.design_contract import parse_design
from app.domain.architecture.tools import register_architecture_tools
from app.execution_context import ExecutionContext, ExecutionMode
from app.tool_manager.gateway import ToolGateway
from app.tool_manager.source import ToolExecutionError


def _design():
    return {
        "schema_version": 1,
        "design_id": "todo-blueprint",
        "depth": 0,
        "system_boundary": "用户通过 HTTP 使用可持久化的任务服务",
        "layers": [{"name": "api", "path_mapping": ["backend/**"]}],
        "modules": [{
            "module_id": "api",
            "boundary_role": "business_capability",
            "purpose": "管理任务",
            "responsibility": "提供 HTTP API",
            "owned_required_files": ["backend/main.py"],
        }],
        "required_files": ["backend/main.py"],
    }


def _context():
    return ExecutionContext(
        trace_id="tr-relay-commit", work_item_id="wi-blueprint",
        agent_id="architecture_agent", execution_mode=ExecutionMode.PARTITIONED,
        slot="blueprint", contract_digest="a" * 64,
        architecture_config=ArchitectureExecutionConfig(scheme="D"),
    )


def test_relay_design_envelope_reaches_real_gateway_and_commits(tmp_path):
    gateway = ToolGateway()
    register_architecture_tools(gateway, str(tmp_path))
    context = _context()
    assert gateway.preflight("architecture", ("write_architecture_blueprint",), context=context).passed

    class RelayAgent:
        def __init__(self, **kwargs):
            self.tools = kwargs["tools"]

        def execute_task(self, _task):
            return (
                "已修正蓝图，开始写入。"
                '{"summary":"修正完成","work_stage":"write"}'
                + json.dumps({"design": _design()}, ensure_ascii=False)
                + '{"type":"capability_request","capability":"write_architecture_blueprint",'
                '"reason":"relay 错误宣称工具不可见"}'
            )

    with patch("app.agent.base_agent.Agent", RelayAgent), patch(
        "app.agent.base_agent.Task", side_effect=lambda **kw: kw
    ), patch("app.agent.base_agent.build_llm", return_value=object()):
        result = BaseAgent(
            gateway, "architecture", "架构师", "写入蓝图", "按合同提交"
        ).run("写入 blueprint", context=context)

    assert result.status is AgentStatus.COMPLETED
    repository = ArtifactRepository(str(tmp_path))
    ref = ArtifactRef.staged(artifact_key="architecture", trace_id=context.trace_id, work_item_id=context.work_item_id, slot="blueprint")
    staged = parse_design(repository.load_ref(ref))
    assert staged.architecture_scheme == "D"
    assert staged.modules[0].owned_required_files == ["backend/main.py"]
    receipt = repository.verify_staged(
        ref, expected_trace_id=context.trace_id,
        expected_work_item_id=context.work_item_id, expected_slot="blueprint",
        expected_artifact_kind="architecture_design",
        expected_source_refs=(), expected_contract_digest=context.contract_digest,
    )
    assert repository.load_commit_receipt(ref).digest == receipt.digest
    assert (tmp_path / ".projectos/runs/tr-relay-commit/work-items/wi-blueprint/output/blueprint.md").is_file()
    assert (tmp_path / ".projectos/architecture/validation-receipts/tr-relay-commit/wi-blueprint/blueprint.json").is_file()


def test_blueprint_wire_omission_is_bound_to_trace_scheme(tmp_path):
    gateway = ToolGateway()
    register_architecture_tools(gateway, str(tmp_path))
    tool = next(t for t in gateway.tools_for("architecture", context=_context())
                if t.name == "write_architecture_blueprint")
    # The model-facing schema leaves architecture_scheme nullable. The trusted
    # context must bind the omitted field to the D execution scheme.
    result = tool.run(design=_design())
    assert "staged:tr-relay-commit:wi-blueprint:blueprint" in result
    ref = ArtifactRef.staged(artifact_key="architecture", trace_id="tr-relay-commit",
                             work_item_id="wi-blueprint", slot="blueprint")
    stored = parse_design(ArtifactRepository(str(tmp_path)).load_ref(ref))
    assert stored.architecture_scheme == "D"


def test_blueprint_rejects_invalid_stack_at_producer_boundary(tmp_path):
    gateway = ToolGateway()
    register_architecture_tools(gateway, str(tmp_path))
    bad = _design()
    bad["modules"][0]["tech_stack"] = ["python", "fastapi"]
    tool = next(t for t in gateway.tools_for("architecture", context=_context())
                if t.name == "write_architecture_blueprint")
    try:
        tool.run(design=bad)
    except ToolExecutionError as error:
        assert error.result.error_type == "tool_validation"
        assert "architecture_tech_stack_validation" in str(error.result.message)
        assert "allowed_stacks" in str(error.result.message)
        assert "fastapi" in str(error.result.message)
    else:
        raise AssertionError("invalid architecture tech stack was staged")
    ref = ArtifactRef.staged(artifact_key="architecture", trace_id="tr-relay-commit",
                             work_item_id="wi-blueprint", slot="blueprint")
    assert not (tmp_path / ".projectos/runs/tr-relay-commit/work-items/wi-blueprint/commit-receipt.json").exists()

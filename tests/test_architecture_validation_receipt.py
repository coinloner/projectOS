from pathlib import Path
import tempfile
from app.domain.architecture.service import ArchitectureArtifactWorkflow
from app.domain.architecture.validation import ValidationReceiptStore
from app.execution_context import ExecutionContext, ExecutionMode


def test_d_design_write_persists_receipt_bound_to_input_digest():
    with tempfile.TemporaryDirectory() as project:
        workflow = ArchitectureArtifactWorkflow(project)
        context = ExecutionContext(
            trace_id="tr-receipt",
            work_item_id="wi-blueprint",
            agent_id="architecture_agent",
            execution_mode=ExecutionMode.PARTITIONED,
            slot="blueprint",
            architecture_config=__import__(
                "app.architecture_execution_config", fromlist=["ArchitectureExecutionConfig"]
            ).ArchitectureExecutionConfig(scheme="D"),
        )
        design = {
            "schema_version": 1,
            "architecture_scheme": "D",
            "design_id": "blueprint",
            "depth": 0,
            "system_boundary": "任务管理能力",
            "layers": [{"name": "application", "path_mapping": ["backend/**"]}],
            "modules": [{
                "module_id": "task-management",
                "boundary_role": "business_capability",
                "responsibility": "管理任务生命周期",
                "purpose": "让用户创建和完成任务",
            }],
        }
        result = workflow.write_staged_design(context, design)
        assert "staged:tr-receipt:wi-blueprint:blueprint" in result
        ref = context.input_refs
        store = ValidationReceiptStore(project)
        staged = next(
            p for p in (  # locate the deterministic receipt without relying on an internal path
                (Path(project) / ".projectos/architecture/validation-receipts/tr-receipt/wi-blueprint").glob("*.json")
            )
        )
        assert staged.is_file()

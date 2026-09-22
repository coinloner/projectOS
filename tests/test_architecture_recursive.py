import tempfile
from pathlib import Path
import pytest
from app.domain.architecture.recursive import RecursiveNode, RecursiveSnapshot, RecursiveSnapshotStore, validate_leaf


def test_recursive_leaf_is_bounded_and_snapshot_is_atomic():
    leaf = RecursiveNode(
        node_id="tasks-service", parent_id="tasks", objective="实现任务能力",
        boundary="leaf", owned_files=("backend/tasks/service.py",),
        acceptance=("可创建任务",), provided_interfaces=("tasks.create",), input_digest="parent-v1",
    )
    validate_leaf(leaf, parent_input_digest="parent-v1", occupied_files=set())
    with tempfile.TemporaryDirectory() as project:
        store = RecursiveSnapshotStore(project)
        snapshot = RecursiveSnapshot(
            schema_version=1, protocol_version=store.protocol_version,
            trace_id="tr", plan_id="plan", requirement_ref="published:requirement:1",
            requirement_digest="req", blueprint_ref="staged:tr:blueprint:blueprint",
            blueprint_digest="bp", accepted_nodes=(leaf,), current_node_ids=(leaf.node_id,),
        )
        store.save(snapshot)
        assert store.load().digest == snapshot.digest


def test_recursive_snapshot_rejects_stale_protocol(tmp_path: Path):
    path = tmp_path / ".projectos/architecture"
    path.mkdir(parents=True)
    (path / "recursive-snapshot.json").write_text('{"protocol_version":"old"}')
    with pytest.raises(ValueError, match="stale"):
        RecursiveSnapshotStore(str(tmp_path)).load()

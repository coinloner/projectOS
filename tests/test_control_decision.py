import tempfile
import pytest
from app.orchestration.control_decision import ControlDecision, ControlDecisionStore

def test_control_decision_is_explicit_and_durable():
    decision=ControlDecision("d1","tr","p",1,"wi-leaf","failure","rebuild_architecture_subgraph",("wi-module",),reason="parent contract conflict")
    with tempfile.TemporaryDirectory() as project:
        store=ControlDecisionStore(project); store.append(decision)
        assert store.list()[0].as_dict()["action"] == "rebuild_architecture_subgraph"

def test_unknown_action_fails_closed():
    with pytest.raises(ValueError):
        ControlDecision("d1","tr","p",1,"wi","f","retry_parent")

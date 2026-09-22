from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.tool_manager.crewai_adapter import ProjectOSTool
from scripts.architecture_comparison_faults import FaultProbe, SCENARIOS


@pytest.mark.parametrize('scenario', [s for s in SCENARIOS if s != 'none'])
def test_fault_boundary_count_scope_and_restoration(scenario):
    name, boundary, count = SCENARIOS[scenario]
    calls = []
    events = []
    def original(tool, **arguments):
        calls.append(tool._context.trace_id)
        return 'saved'
    probe = FaultProbe(scenario, 'target', events.append)
    target = SimpleNamespace(name=name, _context=SimpleNamespace(trace_id='target'))
    other = SimpleNamespace(name=name, _context=SimpleNamespace(trace_id='other'))
    with patch.object(ProjectOSTool, '_run', original):
        with probe.installed():
            assert ProjectOSTool._run(other) == 'saved'
            for _ in range(count):
                with pytest.raises(RuntimeError, match='Experiment-injected'):
                    ProjectOSTool._run(target)
            assert ProjectOSTool._run(target) == 'saved'
        assert ProjectOSTool._run is original
    assert calls.count('target') == (1 if boundary == 'before' else count + 1)
    assert probe.evidence()['fully_exercised']
    assert len([e for e in events if e['kind'] == 'fault_injected']) == count


def test_unreached_boundary_is_not_exercised():
    assert not FaultProbe('pre-formal-commit', 't', lambda _: None).evidence()['fully_exercised']

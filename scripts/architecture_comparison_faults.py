"""Process-local experiment instrumentation; never installed by production runtime.

These are tool-call failures, NOT worker crashes or simulated provider outages.
A boundary that is never reached is reported as unexercised, not successful.
"""
from contextlib import contextmanager
import hashlib
import json
import time
from unittest.mock import patch

from app.tool_manager.crewai_adapter import ProjectOSTool

SCENARIOS = {
    'none': (None, None, 0),
    'pre-input-read': ('load_architecture_input', 'before', 1),
    'post-input-read': ('load_architecture_input', 'after', 1),
    'pre-formal-commit': ('write_module_design', 'before', 1),
    'pre-formal-commit-twice': ('write_module_design', 'before', 2),
    'post-formal-commit': ('write_module_design', 'after', 1),
}


class FaultProbe:
    def __init__(self, scenario, trace_id, emit):
        self.target, self.boundary, self.limit = SCENARIOS[scenario]
        self.scenario = scenario
        self.trace_id = trace_id
        self.emit = emit
        self.injected = 0
        self.events = []

    def record(self, **event):
        event = dict(sequence=len(self.events) + 1, monotonic=time.monotonic(), **event)
        self.events.append(event)
        self.emit(event)

    def hit(self, name, boundary):
        if name == self.target and boundary == self.boundary and self.injected < self.limit:
            self.injected += 1
            self.record(kind='fault_injected', tool=name, boundary=boundary,
                        injection=self.injected, scenario=self.scenario)
            raise RuntimeError('Experiment-injected tool failure: ' + self.scenario)

    @contextmanager
    def installed(self):
        original = ProjectOSTool._run
        probe = self

        def run(tool, **arguments):
            context = tool._context
            if context is None or context.trace_id != probe.trace_id:
                return original(tool, **arguments)
            name = tool.name
            probe.record(kind='tool_enter', tool=name,
                         arguments_digest=hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest(),
                         phase=arguments.get('phase'))
            try:
                probe.hit(name, 'before')
                result = original(tool, **arguments)
                extra = {}
                if name == 'load_architecture_intermediate':
                    extra['checkpoint_result'] = json.loads(result)
                probe.record(kind='tool_return', tool=name, **extra)
                probe.hit(name, 'after')
                return result
            except Exception as error:
                probe.record(kind='tool_error', tool=name, error_type=type(error).__name__, error=str(error))
                raise

        with patch.object(ProjectOSTool, '_run', run):
            yield self

    def evidence(self):
        return dict(scenario=self.scenario, fault_type='tool_call_failure',
                    expected_injections=self.limit, actual_injections=self.injected,
                    fully_exercised=self.injected == self.limit, events=self.events)

"""API selection survives Trace persistence and isolated Worker bootstrap."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.application.runs import StartedRun, _worker_entry
from app.architecture_execution_config import ArchitectureExecutionConfig
from app.bootstrap.runtime import build_container
from app.orchestration.trace import TraceStore
from app.project.project import Project


def test_api_explicit_checkpoint_mode_is_passed_to_run_service(tmp_path):
    with TestClient(create_app(projects_root=str(tmp_path))) as client:
        assert client.post('/api/v1/projects', json={'name': 'demo'}).status_code == 201
        service = client.app.state.run_service
        result = StartedRun(trace_id='tr-test', plan_id='run-test', workflow_id='delivery_default', status='running')
        with patch.object(service, 'start_controlled_workflow', return_value=result) as start:
            response = client.post('/api/v1/projects/demo/runs', json={
                'goal': '交付项目', 'workflow_id': 'delivery_default', 'architecture_mode': 'checkpointed'
            })
            assert response.status_code == 202, response.text
            assert response.json()['trace_id'] == 'tr-test'
            assert start.call_args.kwargs['architecture_config'] == ArchitectureExecutionConfig(
                mode='checkpointed', scheme='D'
            )
            start.reset_mock()
            assert client.post('/api/v1/projects/demo/runs', json={
                'goal': '交付项目', 'workflow_id': 'delivery_default'
            }).status_code == 202
            assert 'architecture_config' not in start.call_args.kwargs
            start.reset_mock()
            response = client.post('/api/v1/projects/demo/runs', json={
                'goal': '交付项目', 'workflow_id': 'delivery_default',
                'architecture_candidate_strategy': 'incremental_candidate',
            })
            assert response.status_code == 202
            assert start.call_args.kwargs['architecture_config'] == ArchitectureExecutionConfig(
                mode='baseline', scheme='D', candidate_strategy='incremental_candidate'
            )
            start.reset_mock()
            assert client.post('/api/v1/projects/demo/runs', json={
                'goal': '交付项目', 'workflow_id': 'delivery_default', 'architecture_mode': 'unknown'
            }).status_code == 422
            start.assert_not_called()


def test_trace_architecture_config_roundtrip_and_fail_closed(tmp_path):
    Project.create_at(str(tmp_path / 'demo'), name='demo')
    traces = TraceStore(str(tmp_path / 'demo'))
    trace = traces.start_trace('交付项目')
    expected = ArchitectureExecutionConfig(mode='checkpointed', scheme='D', candidate_strategy='incremental_candidate')
    assert traces.load_architecture_config(trace.trace_id) == ArchitectureExecutionConfig(scheme='D')
    assert ArchitectureExecutionConfig(scheme='D').candidate_strategy == 'all_or_nothing'
    assert ArchitectureExecutionConfig(scheme='D', candidate_strategy='incremental_candidate').retains_partial_candidates
    traces.set_architecture_config(trace.trace_id, expected)
    assert TraceStore(traces.project_path).load_architecture_config(trace.trace_id) == expected
    container = build_container(traces.project_path, architecture_config=traces.load_architecture_config(trace.trace_id))
    assert container.runner._architecture_config == expected

    trace_path = traces.trace_root(trace.trace_id) / 'trace.json'
    payload = traces.load_trace(trace.trace_id)
    payload['architecture_config'] = {'mode': 'checkpointed', 'scheme': 'D', 'schema_version': 2}
    traces._write_json(trace_path, payload)
    with pytest.raises(ValueError, match='schema'):
        traces.load_architecture_config(trace.trace_id)


def test_worker_entry_recovers_pinned_profile_before_execution(tmp_path):
    Project.create_at(str(tmp_path / 'demo'), name='demo')
    traces = TraceStore(str(tmp_path / 'demo'))
    trace = traces.start_trace('交付项目')
    expected = ArchitectureExecutionConfig(mode='checkpointed', scheme='D')
    traces.set_architecture_config(trace.trace_id, expected)
    received = []

    def inspect_builder(project_path, **kwargs):
        received.append((project_path, kwargs))
        # This is the Worker duplicate-claim exit, not a delivery failure.
        raise SystemExit(75)

    with patch('app.application.runs.build_container', side_effect=inspect_builder):
        _worker_entry(traces.project_path, trace.trace_id)
    assert len(received) == 1
    assert received[0][0] == traces.project_path
    assert received[0][1]['architecture_config'] == expected

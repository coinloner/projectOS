from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.runs import RunService
from app.llm.config import LLMSelection
from app.llm.preflight import ProviderPreflightError, ProviderPreflightResult
from app.orchestration.plan import ExecutionPlan
from app.orchestration.trace import TraceStore
from app.orchestration.work_item import WorkItem
from app.planner.service import PlannerFailure


@dataclass
class _Template:
    id: str = "controlled-test"
    nodes: tuple = ()


class _Templates:
    def get(self, workflow_id: str):
        return _Template() if workflow_id == "controlled-test" else None


class _Artifacts:
    def exists(self, artifact_key: str) -> bool:
        return False


class _Coordinator:
    def __init__(self) -> None:
        self.submissions: list[tuple[object, object]] = []

    def submit(self, container, plan, state=None):
        self.submissions.append((container, plan))


class _Container:
    def __init__(self, project_path: str) -> None:
        self.traces = TraceStore(project_path)
        self.llm_selection = LLMSelection(
            provider="wanfa",
            model="gpt-6-sol",
            base_url="https://wanfaai.com",
            api_key_env="wanfa_API_KEY",
            crewai_provider="openai",
            wire_api="responses",
        )
        self.llm_overrides = {}
        self.templates = _Templates()
        self.artifacts = _Artifacts()
        trace = self.traces.start_trace("provider preflight test")
        self.plan = ExecutionPlan(
            id="run-preflight-test",
            goal="provider preflight test",
            trace=trace,
            work_items=(
                WorkItem(
                    id="wi-requirement",
                    agent_id="requirement_agent",
                    objective="write requirement",
                    output_key="requirement",
                ),
            ),
            template_id="controlled-test",
        )
        self.planner = SimpleNamespace(
            plan_controlled_workflow=lambda **_: SimpleNamespace(plan=self.plan)
        )


@pytest.fixture
def container_factory(tmp_path: Path):
    container = _Container(str(tmp_path))
    return container


def _result(status: str, *, retryable: bool = False) -> ProviderPreflightResult:
    return ProviderPreflightResult(
        provider="wanfa",
        model="gpt-6-sol",
        wire_api="responses",
        endpoint="https://wanfaai.com/responses",
        status=status,
        retryable=retryable,
        message="preflight result",
        http_status=403 if status == "blocked" else 200,
        terminal_seen=status == "passed",
        tool_schema_accepted=status == "passed",
    )


def test_provider_preflight_pass_is_recorded_before_worker_submission(
    monkeypatch: pytest.MonkeyPatch, container_factory: _Container
) -> None:
    class _PassedPreflight:
        def check(self, selection):
            assert selection.model == "gpt-6-sol"
            return _result("passed")

    monkeypatch.setattr("app.application.runs.ProviderPreflight", _PassedPreflight)
    coordinator = _Coordinator()
    service = RunService(
        coordinator=coordinator,
        container_builder=lambda *_args, **_kwargs: container_factory,
        provider_preflight=True,
    )

    started = service.start_controlled_workflow(
        project_path=str(container_factory.traces.project_path),
        goal="provider preflight test",
        workflow_id="controlled-test",
    )

    assert started.trace_id == container_factory.plan.trace.trace_id
    assert len(coordinator.submissions) == 1
    event_types = [event["type"] for event in container_factory.traces.list_events(started.trace_id)]
    assert event_types[-2:] == ["provider_preflight_started", "provider_preflight_passed"]


def test_provider_preflight_failure_blocks_trace_and_never_submits_worker(
    monkeypatch: pytest.MonkeyPatch, container_factory: _Container
) -> None:
    result = _result("blocked")

    class _BlockedPreflight:
        def check(self, selection):
            raise ProviderPreflightError(result)

    monkeypatch.setattr("app.application.runs.ProviderPreflight", _BlockedPreflight)
    coordinator = _Coordinator()
    service = RunService(
        coordinator=coordinator,
        container_builder=lambda *_args, **_kwargs: container_factory,
        provider_preflight=True,
    )

    with pytest.raises(PlannerFailure) as error_info:
        service.start_controlled_workflow(
            project_path=str(container_factory.traces.project_path),
            goal="provider preflight test",
            workflow_id="controlled-test",
        )

    assert error_info.value.trace_id == container_factory.plan.trace.trace_id
    assert len(coordinator.submissions) == 0
    trace = container_factory.traces.load_trace(container_factory.plan.trace.trace_id)
    assert trace["status"] == "blocked"
    assert trace["error"] == result.message
    events = container_factory.traces.list_events(container_factory.plan.trace.trace_id)
    assert [event["type"] for event in events][-2:] == [
        "provider_preflight_started",
        "provider_preflight_failed",
    ]
    assert events[-1]["details"]["status"] == "blocked"
    assert events[-1]["details"]["retryable"] is False

"""Fresh real-provider Architecture closure; never seeds generated artifacts.

Run with the project virtualenv and --env-file pointing at local credentials.
This exercises requirement -> recursive architecture -> integration -> gate,
not implementation/runtime E2E and not a matched comparative experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from app.artifact.repository import ArtifactRepository
from app.bootstrap.runtime import build_container
from app.execution_context import ExecutionMode
from app.orchestration.trace import TraceStore
from app.llm.config import LLMSelection
from app.project.project import Project


def audit_architecture_publication(path: Path, trace_id: str) -> dict[str, object]:
    """Read-only verification of actual publication and dynamic barrier evidence."""
    plan = TraceStore(str(path)).load_plan(trace_id)
    repository = ArtifactRepository(str(path))
    gates = [item for item in plan.work_items
             if item.execution_mode is ExecutionMode.QUALITY_GATE
             and item.publish_target == "architecture"]
    if len(gates) != 1:
        raise RuntimeError("Expected exactly one Architecture publication gate")
    gate = gates[0]
    producer = plan.work_item(gate.candidate_from_work_item_id or "")
    if producer is None or producer.execution_mode is not ExecutionMode.INTEGRATION:
        raise RuntimeError("Publication gate has no integration producer")
    candidate = repository.candidate_for_work_item(
        trace_id=trace_id, artifact_key="architecture", work_item_id=producer.id,
        expected_source_refs=producer.input_refs,
        expected_contract_digest=producer.contract_digest,
    )
    revision = repository.verify_published_candidate(candidate)
    root = path / ".projectos" / "runs" / trace_id
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    starts = {event["work_item_id"]: index for index, event in enumerate(events)
              if event["type"] == "work_item_started"}
    ends = {event["work_item_id"]: index for index, event in enumerate(events)
            if event["type"] == "work_item_completed"}
    if any(item.id not in ends for item in plan.work_items):
        raise RuntimeError("Not all expanded work items have completion events")
    for item in plan.work_items:
        if item.agent_id == "architecture_agent" and item.execution_mode is ExecutionMode.PARTITIONED:
            if ends[item.id] >= starts[producer.id]:
                raise RuntimeError(f"Integration ran before Architecture producer {item.id}")
    if ends[producer.id] >= starts[gate.id]:
        raise RuntimeError("Gate ran before integration completed")
    return dict(candidate_id=candidate.id, source_count=len(candidate.source_refs),
                revision=revision.ref_id, digest=candidate.digest,
                expanded_work_items=len(plan.work_items),
                all_architecture_producers_before_integration=True,
                integration_before_gate=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--projects-root', type=Path, default=Path('/Users/coinloner/projectOS/project'))
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    if not os.environ.get('wanfa_API_KEY'):
        raise SystemExit('wanfa_API_KEY is not configured; no project or provider request created')
    name = f'wanfa-architecture-protocol-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'
    path = args.projects_root / name
    path.mkdir(parents=True, exist_ok=False)
    Project.create_at(str(path), name=name)
    selection = LLMSelection(provider='wanfa', model='gpt-5.6-terra',
                             base_url='https://wanfaai.com', api_key_env='wanfa_API_KEY',
                             crewai_provider='openai', wire_api='responses')
    container = build_container(str(path), llm_selection=selection)
    goal = ('设计一个小型任务管理应用，最终需有 HTTP API、浏览器交互界面和 SQLite 持久化。'
            '支持创建任务、完成/取消完成、按状态筛选、删除及数量统计；空白标题返回 HTTP 400；'
            '重启后数据保留；提供 /health、自动化测试和启动访问说明。'
            '按业务职责及依赖关系拆分，不预设具体文件名。'
            '本次只完成需求与可供后续实现的架构设计、集成和质量门，不进行代码实现。')
    planning = container.planner.plan_controlled_workflow(
        goal=goal, plan_id=f'run-{uuid4().hex[:12]}', workflow_id='architecture_only')
    plan = planning.plan
    container.traces.set_llm_selection(plan.trace.trace_id, selection)
    evidence = dict(project_path=str(path), trace_id=plan.trace.trace_id,
                    plan_id=plan.id, workflow='architecture_only', provider=selection.as_dict(),
                    scope='real requirement and architecture only; not full E2E', status='running')
    report = path / 'validation-evidence.json'
    report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('VALIDATION_STARTED ' + json.dumps(evidence, ensure_ascii=False), flush=True)
    started = time.monotonic()
    try:
        result = container.runner.run(plan)
        evidence.update(status=result.status.value, error=result.error,
                        nodes={key: value.as_dict() for key, value in result.state.node_results.items()})
        if result.status.value == "completed":
            evidence["publication_audit"] = audit_architecture_publication(path, plan.trace.trace_id)
    except Exception as error:
        evidence.update(status='exception', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        requirement = path / 'requirement.md'
        evidence.update(elapsed_seconds=round(time.monotonic() - started, 3),
                        requirement_sha256=hashlib.sha256(requirement.read_bytes()).hexdigest()
                        if requirement.is_file() else None,
                        metrics=container.traces.metrics(plan.trace.trace_id))
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
        print('VALIDATION_FINISHED ' + json.dumps(evidence, ensure_ascii=False, default=str), flush=True)
    if evidence["status"] != "completed":
        raise SystemExit(1)


if __name__ == '__main__':
    main()

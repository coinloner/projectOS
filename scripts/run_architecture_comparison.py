"""Matched real-provider module pilot. No independent upstream generation or E2E claim."""
from __future__ import annotations
import argparse
from dataclasses import fields
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.artifact.store import ArtifactStore
from app.bootstrap.runtime import build_container
from app.execution_context import ExecutionMode
from app.architecture_execution_config import ArchitectureExecutionConfig
from app.llm.config import LLMSelection
from app.orchestration.plan import ExecutionPlan
from app.orchestration.runner import GraphRunner
from app.orchestration.work_item import WorkItem
from app.project.project import Project
from scripts.freeze_architecture_comparison import verify_bundle, digest
from scripts.architecture_comparison_faults import FaultProbe, SCENARIOS
from scripts.architecture_branch_profile import validate_branch_profile, source_identity


def import_module(bundle: Path, project: Path, module_id: str):
    manifest = verify_bundle(bundle)
    matches = [w for w in manifest['work_items'] if w['slot'] == f'module-{module_id}']
    if len(matches) != 1:
        raise ValueError('Expected exactly one frozen module contract')
    raw = matches[0]
    refs = tuple(ArtifactRef(**ref) for ref in raw['input_refs'])
    inputs = {entry['ref_id']: entry for entry in manifest['inputs']}
    repo = ArtifactRepository(str(project))
    evidence = []
    for ref in refs:
        entry = inputs[ref.ref_id]
        content = (bundle / entry['file']).read_text()
        if ref.layer == 'staged':
            repo.write_staged(trace_id=ref.trace_id, work_item_id=ref.work_item_id,
                artifact_key=ref.artifact_key, slot=ref.slot, content=content)
        elif ref.revision_id is None:
            ArtifactStore(str(project)).save(ref.artifact_key, content)
        else:
            raise ValueError('Pinned published import not implemented')
        actual = digest(repo.load_ref(ref).encode())
        if actual != entry['sha256']:
            raise ValueError('Imported input differs from frozen bytes')
        evidence.append({'ref_id': ref.ref_id, 'sha256': actual})
    kwargs = {f.name: raw[f.name] for f in fields(WorkItem) if f.init and f.name in raw}
    # Producers have already run in the frozen source. Only scheduling dependencies
    # are removed; input identities/bytes and all task constraints remain unchanged.
    kwargs.update(input_refs=refs, dependencies=(), contract_digest=None,
                  execution_mode=ExecutionMode(raw['execution_mode']))
    tuple_fields = ('acceptance_criteria', 'constraints', 'non_goals', 'allowed_paths',
        'forbidden_paths', 'required_paths', 'policy_refs', 'skill_refs', 'requirement_ids',
        'owned_files', 'required_tools')
    for name in tuple_fields:
        kwargs[name] = tuple(kwargs.get(name, ()))
    item = WorkItem(**kwargs)
    return item, {'bundle_digest': manifest['bundle_digest'], 'inputs': evidence,
                  'contract_digest': item.contract_digest, 'source_contract_digest': raw['contract_digest']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--module', required=True)
    parser.add_argument('--arm', choices=['A', 'B'], required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--fault-scenario', choices=tuple(SCENARIOS), default='none')
    parser.add_argument('--group-id', default=None)
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[1]
    branch_profile = validate_branch_profile(checkout, args.arm)
    source = source_identity(checkout) if branch_profile else None
    verify_bundle(args.bundle)
    load_dotenv(args.env_file, override=False)
    if not os.environ.get('wanfa_API_KEY'):
        raise SystemExit('wanfa_API_KEY missing')
    root = Path('/Users/coinloner/projectOS/project')
    name = f'wanfa-matched-{args.arm.lower()}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'
    project = root / name
    project.mkdir(parents=True, exist_ok=False)
    Project.create_at(str(project), name=name)
    item, provenance = import_module(args.bundle, project, args.module)
    selection = LLMSelection(provider='wanfa', model='gpt-5.6-terra', base_url='https://wanfaai.com',
        api_key_env='wanfa_API_KEY', crewai_provider='openai', wire_api='responses')
    container = build_container(str(project), llm_selection=selection)
    trace = container.traces.start_trace('matched module pilot')
    plan = ExecutionPlan(id=f'comparison-{uuid4().hex[:12]}', goal=item.objective,
        work_items=(item,), trace=trace)
    container.traces.set_llm_selection(trace.trace_id, selection)
    architecture_config = ArchitectureExecutionConfig(
        mode="checkpointed" if args.arm == "B" else "baseline")
    runner = GraphRunner(container.agents, container.gateway, traces=container.traces,
        artifacts=container.artifact_repository, memory=container.memory,
        skills=container.skills, policies=container.policies, llm_selection=selection,
        max_workers=1, architecture_config=architecture_config)
    report = dict(scope='matched module natural pilot, not E2E or completed ABC comparison',
        source_identity=source, branch_profile=branch_profile,
        architecture_config=architecture_config.as_dict(),
        architecture_config_digest=architecture_config.digest,
        group_id=args.group_id, fault_scenario=args.fault_scenario,
        arm=args.arm, module=args.module, project=str(project), trace_id=trace.trace_id,
        provider=selection.as_dict(), status='running', **provenance)
    output = project / 'comparison-evidence.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'COMPARISON_STARTED {output}', flush=True)
    probe = FaultProbe(args.fault_scenario, trace.trace_id,
        lambda event: container.traces.record_event(trace, item.id, "comparison_probe", details=event))
    started = time.monotonic()
    try:
        with probe.installed():
            result = runner.run(plan)
        report.update(status=result.status.value, error=result.error)
        if result.status.value == 'completed':
            ref = ArtifactRef.staged(artifact_key='architecture', trace_id=trace.trace_id,
                                    work_item_id=item.id, slot=item.slot)
            report['receipt'] = container.artifact_repository.load_commit_receipt(ref).as_dict()
        report['inputs_unchanged'] = all(digest(container.artifact_repository.load_ref(ref).encode()) == entry['sha256']
            for ref, entry in zip(item.input_refs, provenance['inputs']))
        if not report['inputs_unchanged']:
            report['status'] = 'invalid_experiment'
    except Exception as error:
        report.update(status='exception', error=str(error), error_type=type(error).__name__)
    finally:
        report['fault_evidence'] = probe.evidence()
        report.update(elapsed_seconds=time.monotonic()-started, metrics=container.traces.metrics(trace.trace_id))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f'COMPARISON_FINISHED {report["status"]} {output}', flush=True)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

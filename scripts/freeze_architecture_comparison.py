"""Freeze real upstream outputs for matched module experiments, not E2E evidence."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.orchestration.trace import TraceStore
from scripts.validate_wanfa_architecture import audit_architecture_publication


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()


def verify_bundle(path: Path) -> dict:
    manifest = json.loads((path / 'manifest.json').read_bytes())
    recorded = manifest.pop('bundle_digest')
    if digest(canonical(manifest)) != recorded:
        raise ValueError('Comparison manifest digest mismatch')
    for item in manifest['inputs']:
        name = item['file']
        if Path(name).name != name or (path / name).is_symlink():
            raise ValueError('Unsafe comparison input path')
        if digest((path / name).read_bytes()) != item['sha256']:
            raise ValueError(f'Comparison input digest mismatch: {name}')
    manifest['bundle_digest'] = recorded
    return manifest


def freeze(source: Path, trace_id: str, destination: Path) -> dict:
    # Verify actual published content and producer barriers, not a status label.
    audit = audit_architecture_publication(source, trace_id)
    plan = TraceStore(str(source)).load_plan(trace_id)
    modules = [w for w in plan.work_items if w.stage_id == 'architecture_module']
    if not modules:
        raise ValueError('No real module work items in source')
    repository = ArtifactRepository(str(source))
    refs = {r.ref_id: r for w in modules for r in w.input_refs}
    # Keep original requirement available even where current module contracts omit it.
    # A/B/C must receive the same context policy; do not silently alter input_refs.
    requirement = ArtifactRef.published('requirement')
    refs[requirement.ref_id] = requirement
    payloads = [(ref_id, ref, repository.load_ref(ref).encode())
                for ref_id, ref in sorted(refs.items())]
    manifest = dict(schema_version=1, scope='matched module experiment; not fresh E2E',
                    source_project=str(source.resolve()), source_trace=trace_id,
                    publication_audit=audit, work_items=[asdict(w) for w in modules], inputs=[])
    destination.mkdir(parents=True, exist_ok=False)
    for index, (ref_id, ref, content) in enumerate(payloads):
        name = f'input-{index:03d}.md'
        (destination / name).write_bytes(content)
        manifest['inputs'].append(dict(ref_id=ref_id, ref=asdict(ref), file=name,
                                       sha256=digest(content)))
    manifest['bundle_digest'] = digest(canonical(manifest))
    (destination / 'manifest.json').write_bytes(canonical(manifest))
    return verify_bundle(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--trace', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.source, args.trace, args.destination), ensure_ascii=False, indent=2))

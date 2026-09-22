from dataclasses import asdict
import json

from app.artifact.repository import ArtifactRef, ArtifactRepository
from app.execution_context import ExecutionMode
from app.orchestration.work_item import WorkItem, WorkItemDependency
from scripts.freeze_architecture_comparison import canonical, digest
from scripts.run_architecture_comparison import import_module
from app.orchestration.work_item import DependencySource


def test_matched_import_keeps_only_authorized_input_and_identical_contract(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    ref = ArtifactRef.staged(artifact_key='architecture', trace_id='source-trace',
                            work_item_id='blueprint', slot='blueprint')
    item = WorkItem(id='module-api', agent_id='architecture_agent', objective='design API',
        output_key='module', artifact_key='architecture', execution_mode=ExecutionMode.PARTITIONED,
        stage_id='architecture_module', slot='module-api', input_refs=(ref,),
        dependencies=(WorkItemDependency('blueprint', DependencySource.SYSTEM),))
    manifest = {'work_items': [asdict(item)], 'inputs': []}
    for i, (r, content) in enumerate([(ref, 'actual upstream bytes'),
            (ArtifactRef.published('requirement'), 'not authorized for this node')]):
        name = f'input-{i}.md'
        (bundle / name).write_text(content)
        manifest['inputs'].append(dict(ref_id=r.ref_id, ref=asdict(r), file=name, sha256=digest(content.encode())))
    manifest['bundle_digest'] = digest(canonical(manifest))
    (bundle / 'manifest.json').write_bytes(canonical(manifest))
    a, ae = import_module(bundle, tmp_path / 'A', 'api')
    b, be = import_module(bundle, tmp_path / 'B', 'api')
    assert ae == be
    assert a == b
    assert a.input_refs == item.input_refs
    assert a.dependencies == ()
    for project in ('A', 'B'):
        assert ArtifactRepository(str(tmp_path / project)).load_ref(ref) == 'actual upstream bytes'
        assert not (tmp_path / project / 'requirement.md').exists()

"""Input integrity checks for matched experiments; no model success claims."""
import json
import pytest
from scripts.freeze_architecture_comparison import canonical, digest, verify_bundle


def bundle(tmp_path):
    (tmp_path / 'input-000.md').write_bytes(b'real upstream output')
    manifest = {'inputs': [{'file': 'input-000.md', 'sha256': digest(b'real upstream output')}],
                'work_items': [{'objective': 'same module'}]}
    manifest['bundle_digest'] = digest(canonical(manifest))
    (tmp_path / 'manifest.json').write_bytes(canonical(manifest))
    return manifest


def test_bundle_verified_after_reload(tmp_path):
    expected = bundle(tmp_path)
    assert verify_bundle(tmp_path) == expected


def test_changed_upstream_rejected(tmp_path):
    bundle(tmp_path)
    (tmp_path / 'input-000.md').write_bytes(b'changed')
    with pytest.raises(ValueError, match='input digest'):
        verify_bundle(tmp_path)


def test_changed_task_rejected(tmp_path):
    manifest = bundle(tmp_path)
    manifest['work_items'][0]['objective'] = 'different task'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest digest'):
        verify_bundle(tmp_path)

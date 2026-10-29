"""Generic patches cannot retarget a stored role credential to a new service."""
import json

import pytest

from aikey.config_store import UnsafeConfigurationChange
from test_config_store import store


def configured(tmp_path):
    config, path = store(tmp_path)
    raw = json.loads(path.read_text())
    raw['speech_to_text'] = {'provider': 'openai-compatible', 'model': 'synthetic-whisper',
                             'base_url': 'http://127.0.0.1:8178/v1', 'camera_ids': ['a' * 24],
                             'api_key_file': str(tmp_path / 'state/speech-key')}
    path.write_text(json.dumps(raw))
    for name in ('provider-key', 'embedding-key', 'speech-key'):
        (tmp_path / 'state' / name).write_text('-'.join(('synthetic', name, 'not-issued')))
    return config, path


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and not p.name.endswith('.lock')}


@pytest.mark.parametrize('method', ['preview', 'apply'])
@pytest.mark.parametrize('patch', [
    {'inference': {'provider': 'ollama', 'base_url': 'http://127.0.0.1:11434'}},
    {'embeddings': {'base_url': 'http://127.0.0.1:9191/v1'}},
    {'speech_to_text': {'base_url': 'http://127.0.0.1:9192/v1'}},
    {'database': {'host': '127.0.0.2'}},
    {'database': {'port': 5433}},
    {'database': {'sslrootcert': '/synthetic/other-ca.pem'}},
])
def test_generic_endpoint_retarget_cannot_keep_a_previous_credential(tmp_path, method, patch):
    config, path = configured(tmp_path)
    before = files(tmp_path)
    with pytest.raises(UnsafeConfigurationChange, match='role-specific'):
        getattr(config, method)(config.snapshot().revision, patch)
    assert files(tmp_path) == before
    assert not config.history_dir.exists()


def test_a_same_endpoint_model_change_keeps_its_own_role_key(tmp_path):
    config, path = configured(tmp_path)
    original = json.loads(path.read_text())
    revision = config.snapshot().revision
    config.apply(revision, {'inference': {'model': 'synthetic-vision-two'}})
    after = json.loads(path.read_text())
    assert after['inference']['api_key_file'] == original['inference']['api_key_file']
    assert after['inference']['base_url'] == original['inference']['base_url']
    assert after['device'] == original['device'] and after['controller'] == original['controller']
    assert after['embeddings'] == original['embeddings']
    assert after['speech_to_text'] == original['speech_to_text']


def test_role_scoped_provider_change_drops_old_key_and_remains_reversible(tmp_path):
    config, path = configured(tmp_path)
    before = path.read_bytes()
    selected = {'provider': 'ollama', 'model': 'synthetic-local',
                'base_url': 'http://127.0.0.1:11434', 'max_output_tokens': 128}
    saved = config.apply_inference(config.snapshot().revision, selected)
    assert 'api_key_file' not in json.loads(path.read_text())['inference']
    config.rollback_provider(saved.revision)
    assert path.read_bytes() == before


def test_generic_uncredentialed_destination_change_has_no_secret_to_forward(tmp_path):
    config, path = configured(tmp_path)
    raw = json.loads(path.read_text())
    del raw['embeddings']['bearer_token_file']
    path.write_text(json.dumps(raw))
    config.apply(config.snapshot().revision, {'embeddings': {'base_url': 'http://127.0.0.1:9191/v1'}})
    assert 'bearer_token_file' not in json.loads(path.read_text())['embeddings']

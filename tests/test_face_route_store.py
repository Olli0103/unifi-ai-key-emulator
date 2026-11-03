"""Saved-only local Face route transactions; all configuration/templates fictional."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

import pytest

from aikey.config_store import FaceRouteOutcomeUncertain, RevisionConflict, UnsafeConfigurationChange
from test_config_store import store
from test_key_face_scope_store import configured, no_network  # noqa: F401

NEW = 'http://127.0.0.1:9191'


def test_preview_apply_changes_only_server_preserves_order_defaults_history_and_templates(tmp_path):
    config, path, raw, templates = configured(tmp_path)
    before, biometric = path.read_bytes(), templates.read_bytes()
    status = config.face_route_status()
    assert status['configured'] and status['camera_count'] == 2
    assert set(status) == {'configured', 'revision', 'server', 'camera_count'}
    preview = config.preview_face_route(status['revision'], NEW + '/')
    assert preview.changed_fields == ('face_recognition.server',) and preview.restart_required
    assert path.read_bytes() == before and not config.history_dir.exists()
    saved = config.apply_face_route(status['revision'], NEW)
    expected = deepcopy(raw)
    expected['face_recognition']['server'] = NEW
    assert json.loads(path.read_bytes()) == expected and saved.revision == preview.resulting_revision
    assert (config.history_dir / (status['revision'] + '.json')).read_bytes() == before
    assert templates.read_bytes() == biometric
    assert 'max_faces' not in json.loads(path.read_bytes())['face_recognition']
    assert not config.provider_rollback_status()['available']
    assert path.stat().st_mode & 0o777 == config.lock_path.stat().st_mode & 0o777 == 0o600
    assert config.history_dir.stat().st_mode & 0o777 == 0o700


def test_same_canonical_server_is_byte_exact_noop_without_history(tmp_path):
    config, path, _, _ = configured(tmp_path)
    before = path.read_bytes()
    revision = config.snapshot().revision
    preview = config.preview_face_route(revision, 'http://127.0.0.1:8179/')
    assert not preview.changed_fields and not preview.restart_required
    assert config.apply_face_route(revision, 'http://127.0.0.1:8179').revision == revision
    assert path.read_bytes() == before and not config.history_dir.exists()


@pytest.mark.parametrize('server', [None, {}, 1, '', 'https://127.0.0.1',
    'http://public.invalid', 'http://8.8.8.8', 'http://169.254.169.254', 'http://0.0.0.0',
    'http://[::]', 'http://[fe80::1]', 'http://127.0.0.1:0', 'http://127.0.0.1/v1/faces',
    'http://user:password@127.0.0.1', 'http://127.0.0.1?key=fictional',
    'http://127.0.0.1#fragment', 'http://127.0.0.1\n', 'http://127.0.0.1/' + 'x' * 512])
def test_unsupported_route_cannot_save_archive_or_touch_templates(tmp_path, server):
    config, path, _, templates = configured(tmp_path)
    before, biometric = path.read_bytes(), templates.read_bytes()
    revision = config.snapshot().revision
    for operation in [config.preview_face_route, config.apply_face_route]:
        with pytest.raises(UnsafeConfigurationChange):
            operation(revision, server)
    assert path.read_bytes() == before and templates.read_bytes() == biometric
    assert not config.history_dir.exists()


@pytest.mark.parametrize('revision', [None, 1, '', 'a' * 63, 'a' * 64 + '\n', 'f' * 64])
def test_bad_or_stale_revision_never_changes_state(tmp_path, revision):
    config, path, _, _ = configured(tmp_path)
    before = path.read_bytes()
    with pytest.raises(RevisionConflict):
        config.apply_face_route(revision, NEW)
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_absent_role_cannot_be_created_by_a_server_change(tmp_path):
    config, path = store(tmp_path)
    before = path.read_bytes()
    assert config.face_route_status()['configured'] is False
    with pytest.raises(UnsafeConfigurationChange):
        config.apply_face_route(config.snapshot().revision, NEW)
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_duplicate_saved_role_is_not_silently_selected(tmp_path):
    config, path, raw, _ = configured(tmp_path)
    path.write_text(json.dumps(raw)[:-1] + ',"face_recognition":' + json.dumps(raw['face_recognition']) + '}')
    before = path.read_bytes()
    for operation in [config.face_route_status,
                      lambda: config.apply_face_route(config.snapshot().revision, NEW)]:
        with pytest.raises(UnsafeConfigurationChange):
            operation()
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_two_same_revision_routes_have_one_winner(tmp_path):
    config, path, raw, templates = configured(tmp_path)
    revision = config.snapshot().revision
    gate = threading.Barrier(2)
    def save(port):
        gate.wait(timeout=5)
        try:
            config.apply_face_route(revision, f'http://127.0.0.1:{port}')
            return 'saved'
        except RevisionConflict:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(save, 9191), pool.submit(save, 9192)
        assert sorted([a.result(timeout=6), b.result(timeout=6)]) == ['refused', 'saved']
    final = json.loads(path.read_bytes())
    assert final['face_recognition']['camera_ids'] == raw['face_recognition']['camera_ids']
    assert templates.read_bytes() == b'fictional-template-canary-not-to-be-parsed'
    assert len(list(config.history_dir.iterdir())) == 1


def test_post_replace_failure_keeps_new_route_and_reports_uncertain(tmp_path, monkeypatch):
    config, path, _, templates = configured(tmp_path)
    original = config._replace
    def uncertain(content):
        original(content)
        raise OSError('fictional lost acknowledgement')
    monkeypatch.setattr(config, '_replace', uncertain)
    with pytest.raises(FaceRouteOutcomeUncertain, match='outcome is uncertain'):
        config.apply_face_route(config.snapshot().revision, NEW)
    assert json.loads(path.read_bytes())['face_recognition']['server'] == NEW
    assert templates.read_bytes() == b'fictional-template-canary-not-to-be-parsed'

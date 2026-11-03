"""Existing local Key face scope removal only; synthetic state, never media."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import socket
import threading

import pytest

from aikey.config_store import ConfigurationStoreError, RevisionConflict, UnsafeConfigurationChange
from test_config_store import store


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Saved face scope must not request a model, controller or media')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def configured(root):
    config, path = store(root)
    raw = json.loads(path.read_text())
    raw['face_recognition'] = {'server': 'http://127.0.0.1:8179',
                               'camera_ids': ['b' * 24, 'a' * 24], 'native_face_cameras': 'skip'}
    raw['face_enhancement'] = {'server': 'http://127.0.0.1:8180'}
    raw['metadata'] = {'opaque': 'FICTIONAL-PRIVATE-CANARY'}
    path.write_text(json.dumps(raw, indent=1) + '\n')
    state = path.parent / 'state'
    faces = state / 'faces'
    faces.mkdir(mode=0o700)
    (faces / 'identities.json').write_bytes(b'fictional-template-canary-not-to-be-parsed')
    (faces / 'identities.json').chmod(0o600)
    return config, path, raw, faces / 'identities.json'


def test_restrict_then_disable_preserves_every_other_raw_value_and_template_bytes(tmp_path):
    config, path, raw, templates = configured(tmp_path)
    before = path.read_bytes()
    identity_before = templates.read_bytes()
    status = config.face_scope_status()
    assert status['configured'] and status['camera_ids'] == ('b' * 24, 'a' * 24)
    preview = config.preview_face_restriction(status['revision'], ['a' * 24])
    assert preview.changed_fields == ('face_recognition.camera_ids',) and preview.restart_required
    assert path.read_bytes() == before and not config.history_dir.exists()
    saved = config.restrict_faces(status['revision'], ['a' * 24])
    expected = deepcopy(raw)
    expected['face_recognition']['camera_ids'] = ['a' * 24]
    assert saved.revision == preview.resulting_revision and json.loads(path.read_bytes()) == expected
    assert (config.history_dir / (status['revision'] + '.json')).read_bytes() == before
    assert 'max_faces' not in json.loads(path.read_bytes())['face_recognition']
    assert templates.read_bytes() == identity_before
    prior = path.read_bytes()
    preview = config.preview_face_restriction(saved.revision, [])
    result = config.restrict_faces(saved.revision, [])
    del expected['face_recognition']
    assert preview.changed_fields == ('face_recognition',) and result.revision == preview.resulting_revision
    assert json.loads(path.read_bytes()) == expected and not config.face_scope_status()['configured']
    assert (config.history_dir / (saved.revision + '.json')).read_bytes() == prior
    assert templates.read_bytes() == identity_before and not config.provider_rollback_status()['available']
    assert path.stat().st_mode & 0o777 == config.lock_path.stat().st_mode & 0o777 == 0o600
    assert config.history_dir.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in config.history_dir.iterdir())


def test_same_set_in_different_order_is_exact_noop_without_history_or_default_materialization(tmp_path):
    config, path, _, _ = configured(tmp_path)
    before = path.read_bytes()
    revision = config.snapshot().revision
    preview = config.preview_face_restriction(revision, ['a' * 24, 'b' * 24])
    assert not preview.changed_fields and not preview.restart_required
    assert config.restrict_faces(revision, ['a' * 24, 'b' * 24]).revision == revision
    assert path.read_bytes() == before and not config.history_dir.exists()


@pytest.mark.parametrize('retained', [None, {}, (), 'a' * 24, [True], [1], ['c' * 24],
                                    ['a' * 24, 'a' * 24], ['x'] * 9, ['a' * 24 + ' ']])
def test_new_unknown_duplicate_or_malformed_selection_never_changes_state(tmp_path, retained):
    config, path, _, templates = configured(tmp_path)
    before = path.read_bytes()
    identity_before = templates.read_bytes()
    with pytest.raises(UnsafeConfigurationChange):
        config.restrict_faces(config.snapshot().revision, retained)
    assert path.read_bytes() == before and templates.read_bytes() == identity_before
    assert not config.history_dir.exists()


@pytest.mark.parametrize('revision', [None, '', 'f' * 64, 'a' * 63, 'a' * 64 + '\n', 1])
def test_unseen_or_malformed_revision_never_saves(tmp_path, revision):
    config, path, _, _ = configured(tmp_path)
    before = path.read_bytes()
    with pytest.raises(RevisionConflict):
        config.restrict_faces(revision, [])
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_unconfigured_policy_is_not_created_or_converted(tmp_path):
    config, path = store(tmp_path)
    before = path.read_bytes()
    assert config.face_scope_status()['camera_ids'] is None
    assert not config.face_scope_status()['configured']
    for retained in ([], ['a' * 24]):
        with pytest.raises(UnsafeConfigurationChange):
            config.restrict_faces(config.snapshot().revision, retained)
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_duplicate_json_policy_is_not_silently_selected_for_scope_changes(tmp_path):
    config, path, raw, _ = configured(tmp_path)
    encoded = json.dumps(raw)
    path.write_text(encoded[:-1] + ',"face_recognition":' + json.dumps(raw['face_recognition']) + '}')
    before = path.read_bytes()
    revision = config.snapshot().revision  # The new operation must use its strict source reader.
    with pytest.raises(UnsafeConfigurationChange):
        config.face_scope_status()
    with pytest.raises(UnsafeConfigurationChange):
        config.restrict_faces(revision, [])
    assert path.read_bytes() == before and not config.history_dir.exists()


def test_two_same_revision_restrictions_have_one_winner(tmp_path):
    config, path, _, _ = configured(tmp_path)
    revision = config.snapshot().revision
    gate = threading.Barrier(2)
    def save(camera):
        gate.wait(timeout=5)
        try:
            config.restrict_faces(revision, [camera])
        except RevisionConflict:
            return 'refused'
        return 'saved'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(save, 'a' * 24), pool.submit(save, 'b' * 24)
        assert sorted([a.result(timeout=6), b.result(timeout=6)]) == ['refused', 'saved']
    assert len(json.loads(path.read_bytes())['face_recognition']['camera_ids']) == 1


def test_postcommit_failure_is_uncertain_and_never_automatically_restored(tmp_path, monkeypatch):
    config, path, _, templates = configured(tmp_path)
    original = config._replace
    def uncertain(content):
        original(content)
        raise OSError('fictional lost acknowledgement')
    monkeypatch.setattr(config, '_replace', uncertain)
    with pytest.raises(ConfigurationStoreError, match='outcome is uncertain'):
        config.restrict_faces(config.snapshot().revision, [])
    assert 'face_recognition' not in json.loads(path.read_bytes())
    assert templates.read_bytes() == b'fictional-template-canary-not-to-be-parsed'

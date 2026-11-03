"""Saved capture allowlists, never claimed as native camera pairing."""
from copy import deepcopy
import json
import sys

import pytest

from aikey.device_camera_pool import candidate
from aikey.device_workflow import DeviceWorkflowStore, DeviceOutcomeUncertain
from aikey import device_workflow as module
from aikey.config import atomic_private
from test_aiport_config_store import fixture, CAMERAS
from test_device_workflow import revision

ADDED = '2A1122334499'


def workflow_fixture(root):
    store, path, original = fixture(root)
    return DeviceWorkflowStore('aiport', store, root), path, original


@pytest.mark.parametrize('cameras', [[CAMERAS[0], ADDED], [], list(CAMERAS)])
def test_pool_review_save_exact_undo_preserves_identity_provider_and_raw_bytes(tmp_path, cameras):
    workflow, path, original = workflow_fixture(tmp_path)
    before = path.read_bytes()
    identity = {name: (tmp_path / name).read_bytes() for name in ('device.crt', 'device.key', 'controller-ca.pem')}
    selection = {'cameras': cameras, 'decoder': sys.executable}
    preview = workflow.preview_camera_pool(revision(path), selection)
    assert path.read_bytes() == before
    assert preview.operation == 'camera_pool'
    result = workflow.apply(preview, acknowledgements=set(preview.required_consents))
    assert result['activation'] == 'saved_not_active_runtime'
    saved = json.loads(path.read_bytes())
    if cameras:
        assert saved['live_pool_detector'] == original['live_pool_detector']
        for row in saved['paired_streams']:
            if row['camera_mac'] == ADDED:
                assert row == {'camera_mac': ADDED, 'source_ip': original['controller_ip'],
                               'ffmpeg_path': sys.executable}
    else:
        assert 'live_pool_detector' not in saved and 'paired_streams' not in saved
    assert all((tmp_path / name).read_bytes() == raw for name, raw in identity.items())
    if preview.changed_fields:
        undo = workflow.preview_rollback(revision(path))
        assert 'restore_prior_camera_capture_and_feature_permissions' in undo.required_consents
        assert 'controller_destination_and_pin_reviewed' not in undo.required_consents
        workflow.rollback(undo, acknowledgements=set(undo.required_consents))
    assert path.read_bytes() == before


def test_removed_cameras_lose_only_their_permissions_and_new_cameras_gain_none():
    before = {'controller_ip': '192.168.50.1', 'paired_streams': [
        {'camera_mac': mac, 'source_ip': '192.168.50.1', 'ffmpeg_path': '/bin/ffmpeg'} for mac in CAMERAS],
        'live_pool_detector': {'plate_cameras': [CAMERAS[1]], 'ocr_provider_config': {'api_key_file': '/state/retained'},
            'camera_thresholds': {CAMERAS[0]: .7, CAMERAS[1]: .6}, 'provider_config': {'model': 'unchanged'}},
        'live_speech_cameras': [CAMERAS[1]], 'live_speech_max_events_per_hour': 12,
        'live_face': {'cameras': list(CAMERAS), 'detector_path': '/models/unchanged'},
        'live_sound': {'cameras': [CAMERAS[1]], 'model_path': '/models/retained'}}
    snapshot = deepcopy(before)
    after = candidate(before, {'cameras': [CAMERAS[0], ADDED], 'decoder': '/bin/ffmpeg'})
    assert before == snapshot
    assert after['live_face'] == {'cameras': [CAMERAS[0]], 'detector_path': '/models/unchanged'}
    assert all(name not in after for name in ('live_sound', 'live_speech_cameras', 'live_speech_max_events_per_hour'))
    assert 'plate_cameras' not in after['live_pool_detector'] and 'ocr_provider_config' not in after['live_pool_detector']
    assert after['live_pool_detector']['camera_thresholds'] == {CAMERAS[0]: .7}
    assert after['live_pool_detector']['provider_config'] == before['live_pool_detector']['provider_config']


@pytest.mark.parametrize('cameras', [[ADDED] * 2, ['2a:11:22:33:44:99'], ['invalid'], list(CAMERAS) * 3])
def test_ambiguous_or_unbounded_camera_selection_refuses_before_writes(tmp_path, cameras):
    workflow, path, _ = workflow_fixture(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        workflow.preview_camera_pool(revision(path), {'cameras': cameras, 'decoder': sys.executable})
    assert path.read_bytes() == before and not workflow.journal.exists()


@pytest.mark.parametrize('rollback', [False, True])
def test_uncertain_pool_disable_or_undo_settles_only_with_explicit_reconciliation(tmp_path,monkeypatch,rollback):
    workflow,path,_=workflow_fixture(tmp_path)
    before=path.read_bytes()
    preview=workflow.preview_camera_pool(revision(path),{'cameras':[],'decoder':sys.executable})
    if rollback:
        workflow.apply(preview,acknowledgements=set(preview.required_consents))
        preview=workflow.preview_rollback(revision(path))
    original=module.atomic_private
    def lost_ack(target,raw):
        original(target,raw)
        if target==path:
            raise OSError('synthetic published pool acknowledgement loss')
    monkeypatch.setattr(module,'atomic_private',lost_ack)
    with pytest.raises(DeviceOutcomeUncertain):
        method=workflow.rollback if rollback else workflow.apply
        method(preview,acknowledgements=set(preview.required_consents))
    persisted=path.read_bytes()
    assert workflow.transaction_status()['pending']
    workflow.reconcile(revision(path))
    assert path.read_bytes()==persisted
    assert (persisted==before) is rollback


def test_unrelated_provider_mutation_invalidates_old_camera_undo(tmp_path):
    workflow,path,_=workflow_fixture(tmp_path)
    preview=workflow.preview_camera_pool(revision(path),{'cameras':[CAMERAS[0]],'decoder':sys.executable})
    workflow.apply(preview,acknowledgements=set(preview.required_consents))
    current=json.loads(path.read_bytes())
    current['live_pool_detector']['threshold']=.4
    atomic_private(path,json.dumps(current))
    assert not workflow.transaction_status()['rollback_available']

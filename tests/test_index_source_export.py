"""Exporter through real private files, no controller or model requests."""
import json

import pytest

from aikey.index_source_export import SourceExportError, export_crop_archive
from aikey.index_sources import ObjectCropArchive
from test_index_sources import archive

ROWS = [{'object_id': 'object-a', 'event_id': 'event-a', 'camera_id': 'camera-a'}]
AUTH = frozenset({'camera-a'})


class Native:
    def __init__(self, raw):
        self.raw, self.calls = raw, []
        self.value = {'id': 'event-a', 'camera': 'camera-a', 'metadata': {
            'detectedThumbnails': [{'objectId': 'object-a', 'croppedId': 'crop-a'}]}}

    async def event(self, ident):
        self.calls.append(('event', ident))
        return self.value

    async def crop(self, ident):
        self.calls.append(('crop', ident))
        return self.raw


def native(tmp_path):
    _, _, _, raw, _ = archive(tmp_path)
    return Native(raw)


async def test_exact_export_is_private_terminal_and_consumable(tmp_path):
    client = native(tmp_path)
    target = tmp_path/'new-export'
    receipt = await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
    assert receipt['complete_coverage'] and receipt['exported'] == 1
    assert client.calls == [('event', 'event-a'), ('crop', 'crop-a')]
    for item in [target, *target.rglob('*')]:
        assert item.stat().st_mode & 0o077 == 0
    with ObjectCropArchive(target, manifest_sha256=receipt['manifest_sha256'],
                           authorized_cameras=AUTH, association=lambda _: ('event-a', 'camera-a')) as source:
        assert await source('object-a') == client.raw
    assert json.loads((target/'receipt.json').read_text()) == receipt
    with pytest.raises(FileExistsError):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
    assert len(client.calls) == 2


@pytest.mark.parametrize('mutation', ['camera', 'event', 'duplicate', 'bad-crop', 'metadata'])
async def test_wrong_native_join_stops_before_any_crop_read_or_manifest(tmp_path, mutation):
    client = native(tmp_path)
    if mutation == 'camera':
        client.value['camera'] = 'wrong-camera'
    elif mutation == 'event':
        client.value['id'] = 'wrong-event'
    elif mutation == 'duplicate':
        client.value['metadata']['detectedThumbnails'] *= 2
    elif mutation == 'bad-crop':
        client.value['metadata']['detectedThumbnails'][0]['croppedId'] = '../outside'
    elif mutation == 'metadata':
        client.value['metadata'] = []
    target = tmp_path/'refused-export'
    with pytest.raises(SourceExportError):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
    assert client.calls == [('event', 'event-a')]
    assert not (target/'manifest.json').exists()


async def test_missing_sources_and_unapproved_history_are_not_substituted(tmp_path):
    client = native(tmp_path)
    rows = ROWS + [{'object_id': 'object-b', 'event_id': 'event-b', 'camera_id': 'camera-b'},
                   {'object_id': 'object-c', 'event_id': 'event-c', 'camera_id': 'camera-a'}]
    original = client.event
    async def event(ident):
        return None if ident == 'event-c' else await original(ident)
    client.event = event
    target = tmp_path/'partial-coverage'
    receipt = await export_crop_archive(target, rows, client, authorized_cameras=AUTH)
    assert receipt['indexed'] == 3 and receipt['exported'] == 1
    assert receipt['no_source'] == 1 and receipt['outside_authorization'] == 1
    assert not receipt['complete_coverage']
    assert client.calls == [('event', 'event-a'), ('crop', 'crop-a')]
    manifest = json.loads((target/'manifest.json').read_text())
    assert [r['object_id'] for r in manifest['objects']] == ['object-a']


@pytest.mark.parametrize('mutation', ['duplicate', 'invalid-id', 'extra-field', 'not-a-list', 'no-permission'])
async def test_bad_admission_has_no_filesystem_or_network_effects(tmp_path, mutation):
    client = native(tmp_path)
    rows = [dict(x) for x in ROWS]
    auth = AUTH
    if mutation == 'duplicate':
        rows *= 2
    elif mutation == 'invalid-id':
        rows[0]['event_id'] = '../../event'
    elif mutation == 'extra-field':
        rows[0]['url'] = 'https://unrelated.invalid'
    elif mutation == 'not-a-list':
        rows = {}
    elif mutation == 'no-permission':
        auth = frozenset({'other'})
    target = tmp_path/'refused-export'
    with pytest.raises(SourceExportError):
        await export_crop_archive(target, rows, client, authorized_cameras=auth)
    assert not target.exists() and not client.calls


async def test_budget_exhaustion_preserves_partial_files_without_admission_manifest(tmp_path):
    client = native(tmp_path)
    target = tmp_path/'too-large'
    with pytest.raises(SourceExportError, match='byte budget'):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH, max_bytes=1)
    assert not (target/'manifest.json').exists()


async def test_authentication_failure_is_not_an_unavailable_historical_source(tmp_path):
    client = native(tmp_path)
    async def fail(_):
        raise SourceExportError('The native authentication was refused')
    client.event = fail
    target = tmp_path/'refused-auth'
    with pytest.raises(SourceExportError, match='authentication'):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
    assert not (target/'manifest.json').exists()


async def test_invalid_image_or_stalled_read_does_not_publish_manifest(tmp_path):
    client = native(tmp_path)
    client.raw = b'not-jpeg'
    target = tmp_path/'invalid-image'
    with pytest.raises(SourceExportError, match='bounded JPEG'):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH)
    assert not (target/'manifest.json').exists()
    ticks = iter([0.0, 0.0, 2.0])
    target = tmp_path/'expired'
    (tmp_path/'second').mkdir(mode=0o700)
    client.raw = native(tmp_path/'second').raw
    with pytest.raises(SourceExportError, match='deadline expired'):
        await export_crop_archive(target, ROWS, client, authorized_cameras=AUTH,
                                  timeout=1, clock=lambda: next(ticks))
    assert not (target/'manifest.json').exists()

"""Real scratch SQL plus pinned TLS, generated crops and synthetic encoders only."""
from io import BytesIO

from aiohttp import web
from PIL import Image
import pytest

from aikey import index_rebuild
from aikey.index_readback import NativeReadbackPlan
from aikey.index_source_export import export_crop_archive
from aikey.index_sources import ObjectCropArchive
from test_index_native import authorized, index_session
from test_index_readback import specification
from test_index_rebuild import BACKUP, NEW, OBJECTS, OLD, quiet, vector
from test_index_rebuild_pg import DSN, live, store as sql_store_fixture

from test_native_camera_status import controller

store = sql_store_fixture

pytestmark = pytest.mark.skipif(not DSN, reason='AIKEY_TEST_PG_DSN is not set')


def fixture_images():
    images = {}
    for i, obj in enumerate(OBJECTS):
        out = BytesIO()
        Image.new('RGB', (24, 24), (30*i, 10, 20)).save(out, format='JPEG')
        images['crop-'+obj] = out.getvalue()
    return images


class Encoder:
    def __init__(self, images):
        self.images = images
        self.served = NEW

    async def revision(self):
        return self.served

    async def embed(self, raw):
        key = next(k for k, value in self.images.items() if raw == value)
        return vector(100+int(key.rsplit('-', 1)[1]))

    async def embed_text(self, text):
        return vector(100 if text == 'a red toy car' else 200)


def runtime():
    return {'connected': True, 'revision': NEW, 'profile': 'clip-basic-v1'}


async def test_export_resume_quality_native_reload_and_rollback_without_global_approval(
        store, tmp_path, monkeypatch):
    monkeypatch.setattr(index_rebuild, 'APPROVED_IMAGE_SOURCE', None)
    monkeypatch.setattr(index_rebuild, 'APPROVED_NATIVE_READBACK', None)
    images = fixture_images()
    event_value = {'id': 'event-fixture', 'camera': 'camera-fixture', 'start': 0,
                   'metadata': {'detectedThumbnails': [
                       {'objectId': obj, 'croppedId': 'crop-'+obj} for obj in OBJECTS]}}
    native_reads = []
    async def event(request):
        authorized(request)
        native_reads.append(('event', request.match_info['ident']))
        assert request.match_info['ident'] == 'event-fixture'
        return web.json_response(event_value)
    async def crop(request):
        authorized(request)
        native_reads.append(('crop', request.match_info['ident']))
        return web.Response(body=images[request.match_info['ident']], content_type='image/jpeg')
    async def search(request):
        authorized(request)
        assert request.query['minSimilarity'] == '53'
        positive = request.query['searchText'] == 'a red toy car'
        native_reads.append(('query', positive))
        rows = [{'objectId': 'obj-0', 'eventId': 'event-fixture', 'cameraId': 'camera-fixture',
                 'eventStart': 0, 'detectedAt': 0, 'vectorSimilarity': 60}] if positive else []
        return web.json_response({'detections': rows, 'searchCondition': {}})
    routes = [web.get('/proxy/protect/api/events/{ident}', event),
              web.get('/proxy/protect/api/thumbnails/{ident}', crop),
              web.get('/proxy/protect/api/detection-nls', search)]
    authority = tmp_path/'authority'
    authority.mkdir(mode=0o700)
    old_profile = (tmp_path/'search-profile.json').read_bytes()
    old_vectors = [live(obj) for obj in OBJECTS]
    auth = frozenset({'camera-fixture'})
    target = tmp_path/'native-export'
    async with controller(authority, monkeypatch, additional_routes=routes) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            receipt = await export_crop_archive(target, store.source_rows(), native,
                                                authorized_cameras=auth)
        assert native.logout_acknowledged and receipt['complete_coverage']
        assert receipt['exported'] == 5
        encoder = Encoder(images)
        def generation():
            return index_rebuild.Rebuild(store, tmp_path, target_revision=NEW,
                                        target_source='http://127.0.0.1:8181')
        def source():
            return ObjectCropArchive(target, manifest_sha256=receipt['manifest_sha256'],
                                      authorized_cameras=auth, association=store.source_association)
        with source() as original:
            assert (await generation().stage(encoder, original, batch=2, max_batches=1))['embedded'] == 2
        assert [live(obj) for obj in OBJECTS] == old_vectors
        with source() as original:
            resumed = generation()
            assert (await resumed.stage(encoder, original, batch=2))['embedded'] == 5
            resumed.verify()
            spec = specification()
            spec.update(from_revision=OLD, target_revision=NEW,
                        source_archive_sha256=receipt['manifest_sha256'])
            spec['queries'][0]['expected_objects'] = ['obj-0']
            plan = NativeReadbackPlan(spec)
            quality = await resumed.qualify(encoder, plan)
            assert quality['passed']
            assert [live(obj) for obj in OBJECTS] == old_vectors
            assert (tmp_path/'search-profile.json').read_bytes() == old_profile
            result = generation().cutover(backup=BACKUP, key_quiesced=quiet,
                                          source=original, readback_plan=plan)
            assert result['state'] == 'awaiting_native_readback'
            assert live('obj-0') == vector(100)
        fresh = generation()
        with pytest.raises(index_rebuild.RebuildError, match='readback and acknowledged logout'):
            fresh.finalize_native(logout_acknowledged=False)
        native = index_session(reader)
        async with native:
            result = await generation().confirm_native(plan, native, runtime=runtime)
            assert result['passed']
            assert generation().state == 'awaiting_native_readback'
        assert native.logout_acknowledged
        final = generation().finalize_native(logout_acknowledged=native.logout_acknowledged)
        assert final == {'state': 'cut_over', 'native_verified': True}
        assert generation().finalize_native(logout_acknowledged=True)['already']
        assert (await generation().rollback(key_quiesced=quiet))['rows'] == 5
        assert [live(obj) for obj in OBJECTS] == old_vectors
        assert (tmp_path/'search-profile.json').read_bytes() == old_profile
        assert len([x for x in native_reads if x[0] == 'query']) == 2
        assert seen.count(('POST', '/api/auth/login')) == 2
        assert seen.count(('POST', '/api/auth/logout')) == 2


async def test_sql_projection_join_identity_and_rank_reject_changed_inputs(store, tmp_path, monkeypatch):
    monkeypatch.setattr(index_rebuild, "APPROVED_IMAGE_SOURCE", "synthetic-test-source")
    from test_index_rebuild import Embedder, source
    run = index_rebuild.Rebuild(store, tmp_path, target_revision=NEW,
                               target_source='http://127.0.0.1:8181')
    await run.stage(Embedder(NEW), source())
    assert len(store.source_rows(limit=5)) == 5
    with pytest.raises(index_rebuild.RebuildError, match='complete index projection'):
        store.source_rows(limit=4)
    assert store.source_association('obj-0') == ('event-fixture', 'camera-fixture')
    assert store.source_association('missing') is None
    identity = store.staged_identity(run.tag)
    rows = store.rank_staged(run.tag, vector(100), k=2, min_cosine=.8,
                            camera_id='camera-fixture', start_ms=0, end_ms=1)
    assert [r['object_id'] for r in rows] == ['obj-0']
    assert not store.rank_staged(run.tag, vector(100), k=2, min_cosine=.8,
                                camera_id='wrong-camera', start_ms=None, end_ms=None)
    assert not store.rank_staged(run.tag, vector(100), k=2, min_cosine=.8,
                                camera_id=None, start_ms=1, end_ms=2)
    assert not store.rank_staged(run.tag, vector(200), k=2, min_cosine=.8,
                                camera_id=None, start_ms=None, end_ms=None)
    store.upsert(run.tag, NEW, [('obj-0', 'embedded', vector(110))])
    assert store.staged_identity(run.tag) != identity

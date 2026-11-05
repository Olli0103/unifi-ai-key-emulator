"""Actual pinned TLS/native paths, with fictional loopback authority only."""
import asyncio

from aiohttp import web
import pytest

from aikey.index_native import NativeIndexError, NativeIndexSession
from test_native_camera_status import controller


def index_session(reader):
    return NativeIndexSession(reader.host, username_file=reader.username_file,
        password_file=reader.password_file, trust_file=reader.trust_file, cert_file=reader.cert_file)


def authorized(request):
    assert request.cookies.get('TOKEN') == 'FICTIONAL-SESSION'
    assert request.headers.get('X-CSRF-Token') == 'FICTIONAL-CSRF'


async def test_source_lookup_uses_pinned_session_and_exact_bounded_read_parameters(tmp_path, monkeypatch):
    async def search(request):
        authorized(request)
        assert dict(request.query) == {'searchText': 'image', 'minSimilarity': '0',
            'start': '940000', 'end': '1065000', 'cameras': 'camera-a',
            'offset': '0', 'limit': '1000', 'ignoreTimeFilterFromText': 'true'}
        return web.json_response({'detections': [{'objectId': 'object-a', 'eventId': 'event-a',
            'cameraId': 'camera-a', 'thumbnailId': 'original-a'}]})
    async with controller(tmp_path, monkeypatch, additional_routes=[
            web.get('/proxy/protect/api/detection-nls', search)]) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            result = await native.source_object(
                {'object_id': 'object-a', 'event_id': 'event-a', 'camera_id': 'camera-a'},
                {'id': 'event-a', 'camera': 'camera-a', 'start': 1000000, 'end': 1005000})
            assert result['croppedId'] == 'original-a'
        assert native.logout_acknowledged


async def test_fixed_native_paths_json_crops_and_threshold_are_not_rewritten(tmp_path, monkeypatch):
    reads = []
    async def event(request):
        authorized(request)
        reads.append(request.path)
        return web.json_response({'id': 'event-a', 'camera': 'camera-a'})
    async def crop(request):
        authorized(request)
        reads.append(request.path)
        assert not request.query  # No resize, enhancement or substitution.
        return web.Response(body=b'original-crop-bytes', content_type='image/jpeg')
    async def search(request):
        authorized(request)
        reads.append(request.path)
        assert dict(request.query) == {'searchText': 'a red toy car', 'minSimilarity': '53'}
        return web.json_response({'detections': []})
    routes = [web.get('/proxy/protect/api/events/event-a', event),
              web.get('/proxy/protect/api/thumbnails/crop-a', crop),
              web.get('/proxy/protect/api/detection-nls', search)]
    async with controller(tmp_path, monkeypatch, additional_routes=routes) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            assert native.protect_version == '7.3.74'
            assert (await native.event('event-a'))['id'] == 'event-a'
            assert await native.crop('crop-a') == b'original-crop-bytes'
            assert await native.search('a red toy car', min_similarity=53) == {'detections': []}
        assert native.logout_acknowledged and native._session is None
        assert seen == [('POST', '/api/auth/login'), ('GET', '/proxy/protect/api/bootstrap'),
                        ('POST', '/api/auth/logout')]
        assert len(reads) == 3
        with pytest.raises(NativeIndexError, match='single-use'):
            async with native:
                pass
        with pytest.raises(NativeIndexError, match='closed'):
            await native.event('event-a')


@pytest.mark.parametrize('status', [301, 401, 403, 404, 500])
async def test_only_not_found_becomes_missing_source_without_retry(tmp_path, monkeypatch, status):
    reads = []
    async def event(request):
        reads.append(request.path)
        return web.Response(status=status, headers={'Location': '/leak'})
    async with controller(tmp_path, monkeypatch, additional_routes=[
            web.get('/proxy/protect/api/events/event-a', event)]) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            if status == 404:
                assert await native.event('event-a') is None
            else:
                with pytest.raises(NativeIndexError):
                    await native.event('event-a')
        assert native.logout_acknowledged
        assert len(reads) == 1 and seen[-1] == ('POST', '/api/auth/logout')


@pytest.mark.parametrize('raw', [b'{"id":1,"id":2}', b'{"n":NaN}', b'not json',
                               b' '*(2*1024*1024+1)])
async def test_ambiguous_or_oversized_json_refuses_and_signs_out(tmp_path, monkeypatch, raw):
    async def event(request):
        return web.Response(body=raw, content_type='application/json')
    async with controller(tmp_path, monkeypatch, additional_routes=[
            web.get('/proxy/protect/api/events/event-a', event)]) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            with pytest.raises(NativeIndexError):
                await native.event('event-a')
        assert native.logout_acknowledged and seen[-1] == ('POST', '/api/auth/logout')


@pytest.mark.parametrize('login_status', [401, 403, 500])
async def test_login_refusal_or_uncertainty_has_no_native_reads(tmp_path, monkeypatch, login_status):
    async with controller(tmp_path, monkeypatch, login_status=login_status) as (reader, seen, _):
        native = index_session(reader)
        with pytest.raises(NativeIndexError):
            async with native:
                pytest.fail('must not enter refused session')
        assert ('GET', '/proxy/protect/api/bootstrap') not in seen
        assert seen.count(('POST', '/api/auth/login')) == 1
        assert native._session is None


async def test_unsupported_controller_version_reads_no_event_or_crop(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch, payload={'nvr': {'version': 'unknown'}}) as (reader, seen, _):
        native = index_session(reader)
        with pytest.raises(NativeIndexError, match='unsupported'):
            async with native:
                pytest.fail('unsupported version must refuse')
        assert native.logout_acknowledged
        assert seen[-1] == ('POST', '/api/auth/logout')


async def test_uncertain_logout_is_not_a_success_receipt(tmp_path, monkeypatch):
    async with controller(tmp_path, monkeypatch, logout_status=500) as (reader, seen, _):
        native = index_session(reader)
        with pytest.raises(NativeIndexError, match='cleanup is unconfirmed'):
            async with native:
                pass
        assert not native.logout_acknowledged and native._session is None
        assert seen.count(('POST', '/api/auth/logout')) == 1


async def test_cancelled_native_read_attempts_bounded_logout(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def event(request):
        entered.set()
        await release.wait()
        return web.json_response({})
    async with controller(tmp_path, monkeypatch, additional_routes=[
            web.get('/proxy/protect/api/events/event-a', event)]) as (reader, seen, _):
        native = index_session(reader)
        async def work():
            async with native:
                await native.event('event-a')
        task = asyncio.create_task(work())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert native.logout_acknowledged and native._session is None
            assert seen[-1] == ('POST', '/api/auth/logout')
        finally:
            release.set()


@pytest.mark.parametrize('value', ['../other', 'enhanced/crop', 'https://unrelated.invalid', '', None])
async def test_bad_object_references_are_refused_before_requests(tmp_path, monkeypatch, value):
    async with controller(tmp_path, monkeypatch) as (reader, seen, _):
        async with index_session(reader) as native:
            with pytest.raises(NativeIndexError, match='reference is invalid'):
                await native.crop(value)
            with pytest.raises(NativeIndexError, match='reference is invalid'):
                await native.event(value)
            with pytest.raises(NativeIndexError, match='admission is invalid'):
                await native.search('unrelated', min_similarity=0)
        assert len(seen) == 3  # Login/bootstrap/logout, no attempted source/search read.


async def test_frozen_native_scope_is_sent_to_actual_pinned_endpoint(tmp_path, monkeypatch):
    async def search(request):
        authorized(request)
        assert dict(request.query) == {'searchText': 'a train', 'minSimilarity': '50',
            'cameras': 'camera-a', 'start': '110', 'end': '130', 'ignoreTimeFilterFromText': 'true'}
        return web.json_response({'detections': []})
    async with controller(tmp_path, monkeypatch, additional_routes=[
            web.get('/proxy/protect/api/detection-nls', search)]) as (reader, seen, _):
        native = index_session(reader)
        async with native:
            assert await native.search('a train', min_similarity=50, camera_id='camera-a',
                                       start_ms=110, end_ms=130) == {'detections': []}
            with pytest.raises(NativeIndexError, match='scope'):
                await native.search('a train', min_similarity=50, start_ms=110)
        assert native.logout_acknowledged

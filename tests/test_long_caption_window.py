"""Long native events keep one bounded caption export, never a larger fetch."""

from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qsl, urlencode, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.worker import JobProcessor, WorkerError
from test_basic_descriptions import command
from test_continuous_admission import MutableRegistry, continuous_options
from test_worker import PNG, LocalServices, services as services


def long_event(*, end=301000, moments=(6000, 9000, 290000)):
    item = command()
    body = item['payload']
    body.update(end=end, keyMoments=list(moments))
    path, query = body['reqUrl'].split('?', 1)
    fields = dict(parse_qsl(query))
    fields['end'] = str(end)
    body['reqUrl'] = path + '?' + urlencode(fields)
    return item


@pytest.mark.parametrize('moments', [(6000, 9000, 290000), (301000,), (180000, 290000)])
async def test_long_continuous_caption_fetches_only_one_window_and_keeps_identity(
        services, tmp_path, monkeypatch, moments):
    config = continuous_options(services, tmp_path)
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry('camera-fixture'))
    decoded = []

    async def decode(data, headers, url, job, *, timestamp=None):
        q = dict(parse_qsl(urlsplit(url).query))
        begin, end = int(q['start']), int(q['end'])
        assert 0 < end - begin <= min(30000, worker.max_video_duration_ms)
        assert begin <= timestamp <= end
        assert (job.payload['start'], job.payload['end']) == (begin, end)
        decoded.append(timestamp)
        return PNG

    monkeypatch.setattr(worker, '_video_frame', decode)
    item = long_event(moments=moments)
    original = deepcopy(item)
    try:
        result = await worker.handle(item)
        assert item == original
        assert 1 <= len(decoded) <= worker.max_images
        assert min(moments) in decoded
        assert len(services.media_requests) == len(services.requests) == len(services.callbacks) == 1
        q = dict(parse_qsl(services.video_queries[0]))
        assert q['camera'] == 'camera-fixture' and q['event'] == 'event-fixture'
        assert q['mute'] == 'true' and q['createEvent'] == 'false' and q['format'] == 'mp4'
        assert int(q['end']) - int(q['start']) <= min(30000, worker.max_video_duration_ms)
        assert result['result']['eventId'] == 'event-fixture'
        assert result['result']['cameraId'] == 'camera-fixture'
        assert (await worker.submit(original))['duplicate'] is True
        assert len(services.callbacks) == 1
        changed = deepcopy(original)
        changed['payload']['keyMoments'] = [7000]
        with pytest.raises(WorkerError, match='different input'):
            await worker.submit(changed)
        assert len(services.requests) == 1
    finally:
        await worker.stop()
    restarted = JobProcessor(config, tmp_path, camera_registry=MutableRegistry('camera-fixture'))
    try:
        assert await restarted.handle(original) == result
        assert len(services.media_requests) == len(services.requests) == len(services.callbacks) == 1
    finally:
        await restarted.stop()


@pytest.mark.parametrize('moments', [(100000, 115000), (301000,)])
async def test_real_decoder_reads_the_derived_window_end_not_the_original_event_end(
        tmp_path, moments):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('Real ffmpeg executable unavailable')
    movie = tmp_path / 'fictional-blue-clip.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=64x48:r=2', '-t', '30',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(movie)],
                   check=True, capture_output=True, timeout=15)
    service = LocalServices()
    data = movie.read_bytes()

    async def export(request):
        service.video_queries.append(request.query_string)
        return web.Response(body=data, content_type='video/mp4',
                            headers={'x-start-timestamp': request.query['start']})

    app = web.Application()
    app.router.add_get('/internal/aiprocessors/video/export', export)
    app.router.add_post('/v1/chat/completions', service.model)
    app.router.add_post('/internal/aiprocessors/recognize-anything', service.callback)
    async with TestServer(app) as server:
        service.origin = str(server.make_url('/')).rstrip('/')
        config = continuous_options(service, tmp_path)
        config['worker']['ffmpeg_path'] = str(Path(ffmpeg).resolve())
        worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry('camera-fixture'))
        try:
            result = await worker.handle(long_event(moments=moments))
            assert result['callback'] == 'http_accepted'
            assert len(service.video_queries) == len(service.requests) == len(service.callbacks) == 1
            query = dict(parse_qsl(service.video_queries[0]))
            assert int(query['end']) == max(moments)
            content = service.requests[0]['messages'][0]['content']
            assert len(content) == len(moments) + 1
            assert all(part['image_url']['url'].startswith('data:image/jpeg;base64,')
                       for part in content[1:])
        finally:
            await worker.stop()


@pytest.mark.parametrize('mutation', ['mismatch', 'foreign', 'duplicate', 'over_hour', 'unknown', 'out_of_range'])
async def test_long_window_never_hides_an_invalid_original_contract(services, tmp_path, mutation):
    item = long_event()
    body = item['payload']
    if mutation == 'mismatch':
        body['reqUrl'] = body['reqUrl'].replace('end=301000', 'end=300999')
    elif mutation == 'foreign':
        body['reqUrl'] = 'https://not-approved.invalid' + body['reqUrl']
    elif mutation == 'duplicate':
        body['reqUrl'] += '&camera=camera-fixture'
    elif mutation == 'over_hour':
        item = long_event(end=3601001)
    elif mutation == 'unknown':
        body['_caption_window'] = [1000, 2000]
    else:
        body['keyMoments'] = [301001]
    worker = JobProcessor(continuous_options(services, tmp_path), tmp_path,
                          camera_registry=MutableRegistry('camera-fixture'))
    try:
        with pytest.raises(WorkerError):
            await worker.submit(item)
        assert not services.media_requests and not services.requests and not services.callbacks
        assert not (tmp_path / 'caption-budget.json').exists()
    finally:
        await worker.stop()


async def test_bounded_window_does_not_expand_the_existing_one_use_permit(services, tmp_path):
    from test_basic_descriptions import options
    worker = JobProcessor(options(services), tmp_path)
    try:
        with pytest.raises(WorkerError, match='configured duration bound'):
            await worker.submit(long_event())
        assert worker._read_scope_reservation() is None
        assert not services.media_requests and not services.requests and not services.callbacks
    finally:
        await worker.stop()


async def test_index_targets_outside_the_clip_are_not_decoded_or_posted(
        services, tmp_path, monkeypatch):
    from test_find_anything import roi
    from aikey import clip
    config = continuous_options(services, tmp_path)
    config['search'] = {'enabled': True, 'profile': clip.PROFILE}
    config['find_anything'] = {'clip_server': services.origin,
                              'index_camera_ids': ['camera-fixture'], 'max_objects': 4}
    worker = JobProcessor(config, tmp_path, camera_registry=MutableRegistry('camera-fixture'))
    indexed = []

    async def index(job, data, headers, url):
        assert [target[1] for target in job.payload['_index']] == [6000]
        indexed.extend(target[0] for target in job.payload['_index'])
        return [], [], []

    async def decode(data, headers, url, job, *, timestamp=None):
        assert timestamp in (6000, 9000)
        return PNG

    monkeypatch.setattr(worker, '_index_objects', index)
    monkeypatch.setattr(worker, '_video_frame', decode)
    item = long_event()
    item['payload']['thumbnailMeta'] = [roi(1, 6000, [0, 0, 1000, 1000]),
                                      roi(2, 290000, [0, 0, 1000, 1000])]
    try:
        await worker.handle(item)
        assert indexed == [1]
        changed = deepcopy(item)
        changed['payload']['keyMoments'][-1] = 299000  # still outside the same window
        with pytest.raises(WorkerError, match='different input'):
            await worker.submit(changed)
        assert len(services.media_requests) == len(services.requests) == len(services.callbacks) == 1
    finally:
        await worker.stop()

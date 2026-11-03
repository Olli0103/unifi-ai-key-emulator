"""Requested geometry and recovery counters remain observations, not playback proof."""

import json
from html.parser import HTMLParser

import pytest

from test_processing_camera_names import fixture
from test_control_site_secrets import assert_private_page


class CameraDetails(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cameras = []

    def handle_starttag(self, tag, attributes):
        fields = dict(attributes)
        if tag == 'details' and fields.get('class') == 'processing-camera':
            self.cameras.append(fields)


async def test_geometry_and_recovery_fields_are_visible_without_raw_stream_data(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        value['pool_cameras'][0].update(
            stream_geometry={'width': 1920, 'height': 1080, 'fps': 30}, stream_points=2,
            stream_restart_attempts=4, stream_restart_successes=3, stream_deferred_starts=1,
            stream_full_frames=8, stream_alias='CANARY-private-stream-url')
        value['pool_cameras'][1].update(stream_geometry=None, stream_points=0,
            stream_restart_attempts=0, stream_restart_successes=0, stream_deferred_starts=0,
            stream_full_frames=None)
        data = await (await client.get('/api/admin/v1/processing')).json()
        rows = data['profiles'][1]['runtime']['cameras']
        assert rows[0]['stream_geometry'] == {'width': 1920, 'height': 1080, 'fps': 30}
        assert rows[0]['stream_points'] == 2 and rows[0]['stream_restart_attempts'] == 4
        assert rows[1]['stream_points'] == 0 and rows[1]['stream_full_frames'] is None
        response = await client.get('/processing')
        body = await response.text()
        assert_private_page(response, body)
        for label in ['1920 × 1080 px', 'Requested frame rate', 'Reserved capacity points',
                      'Decoder restart attempts', 'Decoder restart successes', 'Deferred starts',
                      'Full-frame samples', 'not measured camera throughput']:
            assert label in body
        assert 'CANARY' not in body and 'CANARY' not in json.dumps(data)
        assert all(mac not in body for mac in cameras)


async def test_camera_details_are_native_collapsed_and_keep_unknown_vs_false(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        value['pool_cameras'][0]['pending'] = False
        value['pool_cameras'][1]['stream_active'] = None
        body = await (await client.get('/processing')).text()
        parser = CameraDetails()
        parser.feed(body)
        assert len(parser.cameras) == 2 and all('open' not in d for d in parser.cameras)
        assert 'Stream Yes · Policy received Yes · Pending No' in body
        assert 'Stream Unknown · Policy received No · Pending Unknown' in body
        assert '<form' not in body and '<button' not in body


@pytest.mark.parametrize('field', ['stream_points', 'stream_restart_attempts', 'stream_restart_successes',
                                   'stream_deferred_starts', 'stream_full_frames'])
@pytest.mark.parametrize('bad', [True, 'CANARY-private-counter'])
async def test_invalid_stream_measurements_do_not_reach_details(tmp_path, field, bad):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        value['pool_cameras'][0][field] = bad
        data = await (await client.get('/api/admin/v1/processing')).json()
        assert data['profiles'][1]['runtime'] is None and not loads
        assert 'CANARY' not in json.dumps(data)


@pytest.mark.parametrize('fault', ['impossible_size', 'capacity_mismatch', 'invalid_fps'])
async def test_stream_shape_remains_the_existing_strict_requested_geometry_contract(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        camera = value['pool_cameras'][0]
        camera.update(stream_geometry={'width': 1920, 'height': 1080, 'fps': 30}, stream_points=2)
        if fault == 'impossible_size':
            camera['stream_geometry']['width'] = 0
        elif fault == 'capacity_mismatch':
            camera['stream_points'] = 5
        else:
            camera['stream_geometry']['fps'] = 'CANARY-private-fps'
        data = await (await client.get('/api/admin/v1/processing')).json()
        assert data['profiles'][1]['runtime'] is None and not loads
        assert 'CANARY' not in json.dumps(data)

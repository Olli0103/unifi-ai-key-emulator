"""Fictional named runtime rows require the existing exact binding contract."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import time

import pytest

from aikey.admin_runtime import RuntimeObserver
from aikey.camera_bindings import SCHEMA, binding_revision
from aikey.camera_inventory import InventoryError
from test_admin_runtime_observation import target
from test_control_site_secrets import assert_private_page, signed_in


@asynccontextmanager
async def fixture(tmp_path):
    site, server, client, cookie, key = await signed_in(tmp_path)
    peer = None
    try:
        config = json.loads(site.aiport.path.read_text())
        cameras = [row['camera_mac'] for row in config['paired_streams']]
        assert len(cameras) == 2
        value = {'service': 'aiport-candidate', 'adopted': True, 'control_connected': True,
            'pool_binding_schema': SCHEMA,
            'pool_binding_revision': binding_revision(config['mac'], cameras),
            'pool_cameras': [
                {'stream_active': True, 'policy_enabled': True, 'policy_enabled_types': ['person'],
                 'policy_smart_zones': 2, 'policy_exclude_zones': 0, 'policy_secondary_lens_zones': 1,
                 'attempts': 9, 'successes': 8, 'camera': 'CANARY-runtime-private-id',
                 'private_geometry': 'CANARY-zone-coordinates'},
                {'stream_active': False, 'policy_enabled': False, 'policy_enabled_types': [],
                 'policy_smart_zones': None, 'policy_exclude_zones': None,
                 'policy_secondary_lens_zones': None, 'attempts': 0, 'successes': 0}]}
        report = {'schema': 'aikey-camera-preflight/1', 'protect_version': '7.3.74',
            'fetched_at': int(time.time()), 'cameras': [
                {'id': f'{i+1:024x}', 'mac': mac, 'name': name, 'model': 'Fictional camera',
                 'state': 'CONNECTED', 'smart_detect_types': ['person'], 'audio_detect_types': []}
                for i, (mac, name) in enumerate(zip(cameras, ['Fictional hall', 'Fictional garden'], strict=True))]}
        loads = []
        async def load():
            loads.append(True)
            return deepcopy(report)
        site.inventory_loader = load
        peer, calls, url = await target(site.aiport.path.parent, value)
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {'aiport': url})
        yield site, client, value, report, calls, loads, cameras, cookie
    finally:
        if peer is not None:
            await peer.close()
        await client.close()
        await server.close()


async def test_names_are_joined_in_declared_runtime_order_not_inventory_order(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        report['cameras'].reverse()
        before = {p: p.read_bytes() for p in [site.aiport.path, site.aikey.path]}
        response = await client.get('/api/admin/v1/processing')
        data = await response.json()
        rows = data['profiles'][1]['runtime']['cameras']
        assert [row['camera_name'] for row in rows] == ['Fictional hall', 'Fictional garden']
        assert all(row['camera_name_observation'] == 'runtime_order_and_inventory' for row in rows)
        assert data['camera_inventory_fetched_at'] == report['fetched_at']
        text = json.dumps(data)
        assert all(mac not in text for mac in cameras)
        assert all(row['id'] not in text for row in report['cameras'])
        assert 'CANARY' not in text and str(tmp_path) not in text
        response = await client.get('/processing')
        body = await response.text()
        assert_private_page(response, body)
        assert 'Runtime camera 1 · Fictional hall' in body
        assert 'Runtime camera 2 · Fictional garden' in body
        assert '<form' not in body and '<button' not in body
        assert all(p.read_bytes() == raw for p, raw in before.items())
        assert len(calls) == len(loads) == 1


async def test_reported_zones_and_types_survive_without_inventing_native_acceptance(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        response = await client.get('/api/admin/v1/processing')
        rows = (await response.json())['profiles'][1]['runtime']['cameras']
        assert rows[0]['policy_smart_zones'] == 2 and rows[0]['policy_exclude_zones'] == 0
        assert rows[0]['policy_secondary_lens_zones'] == 1
        assert rows[1]['policy_smart_zones'] is None and rows[1]['policy_received'] is False
        body = await (await client.get('/processing')).text()
        assert 'Smart zones (reported)' in body and 'Exclusion zones (reported)' in body
        assert 'Secondary-lens zones (reported)' in body and 'Enabled types (reported)' in body
        assert 'not proof of native pairing' in body
        assert 'not an inside/outside-zone test' in body


@pytest.mark.parametrize('fault', ['reversed_runtime', 'different_device', 'missing_binding',
                                   'reordered_saved', 'wrong_row_count', 'unreadable_saved'])
async def test_unverified_runtime_order_never_selects_inventory_names(tmp_path, fault, monkeypatch):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        if fault == 'reversed_runtime':
            value['pool_binding_revision'] = binding_revision(json.loads(site.aiport.path.read_text())['mac'], list(reversed(cameras)))
        elif fault == 'different_device':
            value['pool_binding_revision'] = binding_revision('0200000000EE', cameras)
        elif fault == 'missing_binding':
            value.pop('pool_binding_revision')
            value.pop('pool_binding_schema')
        elif fault == 'reordered_saved':
            raw = json.loads(site.aiport.path.read_text())
            raw['paired_streams'].reverse()
            site.aiport.path.write_text(json.dumps(raw) + '\n')
        elif fault == 'wrong_row_count':
            value['pool_cameras'].pop()
        else:
            def unavailable():
                raise OSError('CANARY-private-filesystem-path')
            monkeypatch.setattr(site.aiport, '_read', unavailable)
        response = await client.get('/api/admin/v1/processing')
        data = await response.json()
        rows = data['profiles'][1]['runtime']['cameras']
        assert all(row['camera_name'] is None for row in rows)
        expected = ('runtime_order_mismatch' if fault in {'reversed_runtime', 'different_device', 'reordered_saved'}
                    else 'runtime_order_unavailable')
        assert all(row['camera_name_observation'] == expected for row in rows)
        assert rows[0]['attempts'] == 9 and not loads and len(calls) == 1
        text = json.dumps(data)
        assert 'Fictional hall' not in text and 'Fictional garden' not in text and 'CANARY' not in text


@pytest.mark.parametrize('fault', ['stale', 'version', 'duplicate_mac', 'duplicate_id', 'invalid_name',
                                   'not_configured', 'fetch_failure'])
async def test_inventory_failures_remove_names_not_runtime_counters(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        if fault == 'stale':
            report['fetched_at'] = 1
        elif fault == 'version':
            report['protect_version'] = '8.0.0'
        elif fault == 'duplicate_mac':
            report['cameras'][1]['mac'] = report['cameras'][0]['mac']
        elif fault == 'duplicate_id':
            report['cameras'][1]['id'] = report['cameras'][0]['id']
        elif fault == 'invalid_name':
            report['cameras'][0]['name'] = {'private': 'CANARY-invalid-name'}
        elif fault == 'not_configured':
            site.inventory_loader = None
        else:
            async def fail():
                raise InventoryError('CANARY-private-inventory-body')
            site.inventory_loader = fail
        data = await (await client.get('/api/admin/v1/processing')).json()
        rows = data['profiles'][1]['runtime']['cameras']
        assert all(row['camera_name'] is None and row['camera_name_observation'] == 'inventory_unavailable' for row in rows)
        assert rows[0]['attempts'] == 9 and rows[1]['successes'] == 0
        assert data['camera_inventory_fetched_at'] is None
        assert 'CANARY' not in json.dumps(data) and str(tmp_path) not in json.dumps(data)


@pytest.mark.parametrize('fault', ['missing_camera', 'missing_name', 'blank_name', 'mac_name', 'id_name', 'duplicate_name'])
async def test_unusable_names_never_fall_back_to_mac_or_another_camera(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        first = report['cameras'][0]
        if fault == 'missing_camera':
            report['cameras'].pop(0)
        elif fault == 'missing_name':
            first['name'] = None
        elif fault == 'blank_name':
            first['name'] = '   '
        elif fault == 'mac_name':
            first['name'] = 'Camera ' + first['mac']
        elif fault == 'id_name':
            first['name'] = 'Camera ' + first['id']
        else:
            report['cameras'][1]['name'] = '  fictional HALL  '
        data = await (await client.get('/api/admin/v1/processing')).json()
        rows = data['profiles'][1]['runtime']['cameras']
        assert rows[0]['camera_name'] is None
        assert rows[0]['camera_name_observation'] == ('camera_not_in_inventory' if fault == 'missing_camera'
            else 'name_ambiguous' if fault == 'duplicate_name' else 'name_unavailable')
        assert rows[1]['camera_name'] == (None if fault == 'duplicate_name' else 'Fictional garden')
        assert all(mac not in json.dumps(data) for mac in cameras)


async def test_markup_in_an_authoritative_name_is_text_not_dom(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        report['cameras'][0]['name'] = '<img src=x onerror=fictional()> & room'
        response = await client.get('/processing')
        text = await response.text()
        assert '&lt;img src=x onerror=fictional()&gt; &amp; room' in text
        assert '<img ' not in text
        assert_private_page(response, text)


@pytest.mark.parametrize('route,expected', [('/processing', 303), ('/api/admin/v1/processing', 401)])
async def test_revocation_during_inventory_read_returns_no_names_or_counters(tmp_path, route, expected):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, cookie):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked():
            entered.set()
            await release.wait()
            return deepcopy(report)
        site.inventory_loader = blocked
        task = asyncio.create_task(client.get(route, allow_redirects=False))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            site.security.revoke(cookie)
            release.set()
            response = await task
            assert response.status == expected
            text = await response.text()
            assert 'Fictional hall' not in text and 'counter_scope' not in text
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('field', ['policy_smart_zones', 'policy_exclude_zones', 'policy_secondary_lens_zones'])
@pytest.mark.parametrize('bad', [True, -1, 'CANARY-invalid-zone-count'])
async def test_invalid_zone_counter_refuses_peer_without_labeling_it(tmp_path, field, bad):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        value['pool_cameras'][0][field] = bad
        data = await (await client.get('/api/admin/v1/processing')).json()
        assert data['profiles'][1]['runtime'] is None
        assert not loads and 'CANARY' not in json.dumps(data)


async def test_raw_saved_mac_format_does_not_change_normalized_binding(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        raw = json.loads(site.aiport.path.read_text())
        for row in raw['paired_streams']:
            mac = row['camera_mac']
            row['camera_mac'] = ':'.join(mac[i:i+2] for i in range(0, 12, 2)).lower()
        site.aiport.path.write_text(json.dumps(raw) + '\n')
        data = await (await client.get('/api/admin/v1/processing')).json()
        assert [row['camera_name'] for row in data['profiles'][1]['runtime']['cameras']] == ['Fictional hall', 'Fictional garden']


async def test_multiple_matching_profiles_share_one_inventory_observation(tmp_path):
    from aikey.aiport_config_store import AiPortConfigurationStore
    from aikey.tls import ensure_identity_certificate
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        second = tmp_path / 'second-port'
        second.mkdir(mode=0o700)
        raw = json.loads(site.aiport.path.read_text())
        raw['mac'], raw['device_ip'] = '0200000000DD', '192.168.10.41'
        path = second / 'config.json'
        path.write_text(json.dumps(raw) + '\n')
        path.chmod(0o600)
        ensure_identity_certificate(second, raw['mac'])
        ca = second / 'controller-ca.pem'
        ca.write_bytes((site.aiport.path.parent / 'controller-ca.pem').read_bytes())
        ca.chmod(0o600)
        site.aiports['aiport:second'] = AiPortConfigurationStore(path, runtime_state_dir=site.aiport.runtime_state_dir)
        site.aiports['aiport:second']._read()
        other = deepcopy(value)
        other['pool_binding_revision'] = binding_revision(raw['mac'], cameras)
        peer, other_calls, url = await target(second, other)
        old_target = site.runtime_observer.targets['aiport']
        site.runtime_observer = RuntimeObserver(site._backup_profiles(), {'aiport': old_target, 'aiport:second': url})
        try:
            data = await (await client.get('/api/admin/v1/processing')).json()
            ports = data['profiles'][1:]
            assert len(ports) == 2
            assert all([c['camera_name'] for c in p['runtime']['cameras']] == ['Fictional hall', 'Fictional garden'] for p in ports)
            assert len(loads) == len(calls) == len(other_calls) == 1
        finally:
            await peer.close()


async def test_waiting_inventory_lock_has_a_real_deadline_and_keeps_counters(tmp_path):
    async with fixture(tmp_path) as (site, client, value, report, calls, loads, cameras, _):
        await site._inventory_snapshot_lock.acquire()
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(client.get('/api/admin/v1/processing'), timeout=14)
            assert response.status == 200
            data = await response.json()
            assert 11.5 <= time.monotonic() - started < 14
            rows = data['profiles'][1]['runtime']['cameras']
            assert rows[0]['attempts'] == 9 and all(r['camera_name'] is None for r in rows)
            assert all(r['camera_name_observation'] == 'inventory_unavailable' for r in rows)
            assert not loads
        finally:
            site._inventory_snapshot_lock.release()

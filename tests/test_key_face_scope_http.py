"""Private Key saved-scope forms with fictional inventory, audit on from setup."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import re
import threading
import time

from multidict import MultiDict
import pytest

from aikey.admin_audit import AuditLog
from aikey.config_store import ConfigurationStoreError
from test_control_site_secrets import PASSWORD, assert_private_page, signed_in
from test_ocr_provider_http import form


@asynccontextmanager
async def fixture(root):
    site, server, client, cookie, path = await signed_in(root)
    raw = json.loads(path.read_bytes())
    raw['face_recognition'] = {'server': 'http://127.0.0.1:8179',
                               'camera_ids': ['b' * 24, 'a' * 24], 'native_face_cameras': 'skip'}
    raw['face_enhancement'] = {'server': 'http://127.0.0.1:8180'}
    path.write_text(json.dumps(raw, indent=1) + '\n')
    site.audit = AuditLog(root / 'fictional-audit.jsonl')
    report = {'schema': 'aikey-camera-preflight/1', 'protect_version': '7.3.74',
        'fetched_at': int(time.time()), 'cameras': [
            {'id': c * 24, 'mac': '02000000000' + str(i), 'name': name,
             'model': 'UVC G5 Flex', 'state': 'CONNECTED', 'smart_detect_types': [], 'audio_detect_types': []}
            for i, (c, name) in enumerate([('a', 'Fictional garden'),
                ('b', 'Fictional office <script>'), ('c', 'Unapproved camera')], 1)]}
    async def load():
        return deepcopy(report)
    site.inventory_loader = load
    control = site.admin_api.key_face_scope_control
    template = site.aikey_state_dir / 'faces/identities.json'
    template.parent.mkdir(mode=0o700)
    template.write_bytes(b'fictional template canary must not be parsed or changed')
    template.chmod(0o600)
    try:
        yield site, client, cookie, path, control, report, template
    finally:
        await client.close()
        await server.close()


async def preview(site, client, cookie, *, disable=False, all_cameras=False):
    response = await client.get('/key-face-cameras')
    body = await response.text()
    assert response.status == 200
    assert_private_page(response, body)
    data = MultiDict({'csrf': site.security.csrf_token(cookie),
                      'revision': site.aikey.snapshot().revision, 'mode': 'disable_all' if disable else 'restrict'})
    if not disable:
        tokens = re.findall("name='keep_camera' value='([^']+)'", body)
        assert len(tokens) == 2
        for token in tokens if all_cameras else tokens[:1]:
            data.add('keep_camera', token)
    response = await client.post('/key-face-cameras/preview', data=data, headers={'Origin': site.origin})
    assert response.status == 200, await response.text()
    body = await response.text()
    assert_private_page(response, body)
    result = form(body, '/key-face-cameras/apply')
    assert set(result) == {'csrf', 'grant'}
    result['confirm'] = 'yes'
    return result


async def apply(site, client, data):
    return await client.post('/key-face-cameras/apply', data=data, headers={'Origin': site.origin})


async def test_restrict_then_disable_is_saved_only_and_preserves_templates_and_every_other_role(tmp_path, caplog):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, templates):
        before, port_before, identity_before = path.read_bytes(), site.aiport.path.read_bytes(), templates.read_bytes()
        response = await client.get('/key-face-cameras')
        body = await response.text()
        assert 'Fictional office &lt;script&gt;' in body and '<script>' not in body
        assert all(c['id'] not in body and c['mac'] not in body for c in report['cameras'])
        assert '127.0.0.1:8179' not in body
        assert 'separate AI Key restart' in body and 'in-flight matches' in body
        metadata = await (await client.get('/api/admin/v1/key-face-cameras')).json()
        assert metadata['face_scope']['explicit_camera_count'] == 2
        assert metadata['observation'] == 'saved_policy_not_running_authority'
        assert 'camera_ids' not in json.dumps(metadata) and 'server' not in json.dumps(metadata)
        fields = await preview(site, client, cookie)
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()
        response = await apply(site, client, fields)
        assert response.status == 200 and 'Key face restriction saved' in await response.text()
        expected = json.loads(before)
        expected['face_recognition']['camera_ids'] = ['b' * 24]
        assert json.loads(path.read_bytes()) == expected
        assert templates.read_bytes() == identity_before and site.aiport.path.read_bytes() == port_before
        assert not site.aikey.provider_rollback_status()['available']
        assert (await apply(site, client, fields)).status == 409
        assert not control.grants
        fields = await preview(site, client, cookie, disable=True)
        response = await apply(site, client, fields)
        assert response.status == 200
        del expected['face_recognition']
        assert json.loads(path.read_bytes()) == expected and templates.read_bytes() == identity_before
        assert site.aiport.path.read_bytes() == port_before
        audit = site.audit.path.read_text()
        assert all(value not in audit + caplog.text for value in ['Fictional office', '127.0.0.1:8179', fields['grant']])
        assert all(row['action'] == 'key_face_scope' for row in site.audit.recent())


@pytest.mark.parametrize('fault', ['confirm', 'grant', 'extra', 'duplicate', 'csrf', 'origin', 'host',
                                  'expired_session', 'stale_config', 'late_grant', 'renamed',
                                  'new_model', 'stale_inventory', 'duplicate_name', 'query', 'content_type'])
async def test_modified_or_stale_confirmations_never_save(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        fields = await preview(site, client, cookie)
        origin, headers, url = site.origin, {}, '/key-face-cameras/apply'
        if fault == 'confirm':
            fields['confirm'] = 'no'
        elif fault == 'grant':
            fields['grant'] = '0' * 64
        elif fault == 'extra':
            fields['server'] = 'http://127.0.0.1:1'
        elif fault == 'duplicate':
            fields = MultiDict(fields)
            fields.add('grant', fields['grant'])
        elif fault == 'csrf':
            fields['csrf'] = 'wrong'
        elif fault == 'origin':
            origin = 'https://foreign.invalid'
        elif fault == 'host':
            headers['Host'] = 'foreign.invalid'
        elif fault == 'expired_session':
            site.security.revoke(cookie)
        elif fault == 'stale_config':
            value = json.loads(path.read_bytes())
            value['metadata'] = {'fictional': 'concurrent change'}
            path.write_text(json.dumps(value))
        elif fault == 'late_grant':
            control.clock = lambda: float('inf')
        elif fault == 'renamed':
            report['cameras'][1]['name'] = 'Changed fictional name'
        elif fault == 'new_model':
            report['cameras'][1]['model'] = 'Changed fictional model'
        elif fault == 'stale_inventory':
            report['fetched_at'] = 1
        elif fault == 'duplicate_name':
            report['cameras'][2]['name'] = ' fictional OFFICE <script> '
        elif fault == 'query':
            url += '?profile=aiport'
        else:
            headers['Content-Type'] = 'application/json'
        before = path.read_bytes()
        response = await client.post(url, data=fields, headers={'Origin': origin, **headers})
        assert response.status in {403, 409}
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()


@pytest.mark.parametrize('fault', ['missing', 'blank_name', 'duplicate_name', 'mac_name', 'missing_camera'])
async def test_inventory_uncertainty_blocks_individual_editing_but_not_explicit_global_disable(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        if fault == 'missing':
            site.inventory_loader = None
        elif fault == 'blank_name':
            report['cameras'][1]['name'] = '   '
        elif fault == 'duplicate_name':
            report['cameras'][2]['name'] = report['cameras'][1]['name']
        elif fault == 'mac_name':
            report['cameras'][1]['name'] = '02 00 00 00 00 02'
        else:
            report['cameras'].pop(1)
        page = await (await client.get('/key-face-cameras')).text()
        assert 'Individual editing needs' in page and "name='keep_camera'" not in page
        fields = await preview(site, client, cookie, disable=True)
        assert (await apply(site, client, fields)).status == 200
        assert 'face_recognition' not in json.loads(path.read_bytes())


async def test_all_sibling_confirmations_stay_consumed_after_noop_and_exact_byte_restore(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        first = await preview(site, client, cookie, all_cameras=True)
        second = await preview(site, client, cookie, all_cameras=True)
        before = path.read_bytes()
        assert (await apply(site, client, first)).status == 200
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()
        assert not control.grants and (await apply(site, client, second)).status == 409
        a, b = await preview(site, client, cookie), await preview(site, client, cookie)
        assert (await apply(site, client, a)).status == 200
        path.write_bytes(before)  # Fixture restoration only, not a product Undo.
        assert (await apply(site, client, b)).status == 409 and path.read_bytes() == before


@pytest.mark.parametrize('stage', ['previewed', 'admitted', 'saved'])
async def test_configured_audit_failure_is_not_unlogged_success(tmp_path, monkeypatch, stage):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        if stage != 'previewed':
            fields = await preview(site, client, cookie)
        else:
            fields = MultiDict({'csrf': site.security.csrf_token(cookie), 'revision': site.aikey.snapshot().revision,
                                'mode': 'disable_all'})
        original = site.audit.record
        def fail(action, result, **kwargs):
            return False if result == stage else original(action, result, **kwargs)
        monkeypatch.setattr(site.audit, 'record', fail)
        before = path.read_bytes()
        if stage == 'previewed':
            response = await client.post('/key-face-cameras/preview', data=fields, headers={'Origin': site.origin})
            assert not control.grants
        else:
            response = await apply(site, client, fields)
        assert response.status == 503
        if stage == 'saved':
            assert json.loads(path.read_bytes())['face_recognition']['camera_ids'] == ['b' * 24]
            assert 'outcome uncertain' in (await response.text()).lower() and not control.grants
        else:
            assert path.read_bytes() == before


@pytest.mark.parametrize('fault', ['expiry', 'session', 'consumed'])
async def test_authority_is_rechecked_after_awaited_preview(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        fields = await preview(site, client, cookie)
        original = site.aikey.preview_face_restriction
        def late(*args):
            result = original(*args)
            if fault == 'expiry':
                control.clock = lambda: float('inf')
            elif fault == 'session':
                site.security.revoke(cookie)
            else:
                control.grants.clear()
            return result
        monkeypatch.setattr(site.aikey, 'preview_face_restriction', late)
        before = path.read_bytes()
        assert (await apply(site, client, fields)).status in {403, 409}
        assert path.read_bytes() == before and not site.aikey.history_dir.exists()


async def test_postcommit_uncertainty_consumes_all_grants_and_does_not_restore(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        first, second = await preview(site, client, cookie), await preview(site, client, cookie)
        original = site.aikey.restrict_faces
        def lost(*args):
            original(*args)
            raise ConfigurationStoreError('fictional secret diagnostic')
        monkeypatch.setattr(site.aikey, 'restrict_faces', lost)
        response = await apply(site, client, first)
        assert response.status == 503 and 'fictional secret diagnostic' not in await response.text()
        assert json.loads(path.read_bytes())['face_recognition']['camera_ids'] == ['b' * 24]
        assert not control.grants and (await apply(site, client, second)).status == 409


async def test_concurrent_previews_can_admit_only_one_writer(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        first, second = await preview(site, client, cookie), await preview(site, client, cookie)
        entered, release = asyncio.Event(), asyncio.Event()
        original = control._choices
        count = []
        async def blocked(*args, **kwargs):
            result = await original(*args, **kwargs)
            count.append(True)
            if len(count) == 2:
                entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(control, '_choices', blocked)
        a, b = asyncio.create_task(apply(site, client, first)), asyncio.create_task(apply(site, client, second))
        await asyncio.wait_for(entered.wait(), 3)
        release.set()
        responses = await asyncio.gather(a, b)
        assert sorted(r.status for r in responses) == [200, 409]
        assert not control.grants
        assert [r['result'] for r in site.audit.recent()].count('admitted') == 1


async def test_cross_session_choices_and_grants_are_not_authority(tmp_path):
    from aikey.control_site import _COOKIE
    from yarl import URL
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        fields = await preview(site, client, cookie)
        page = await (await client.get('/key-face-cameras')).text()
        choice = re.findall("name='keep_camera' value='([^']+)'", page)[0]
        await client.post('/login', data={'password': PASSWORD}, headers={'Origin': site.origin})
        other = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        assert other != cookie
        csrf = site.security.csrf_token(other)
        fields['csrf'] = csrf
        before = path.read_bytes()
        assert (await apply(site, client, fields)).status == 409
        response = await client.post('/key-face-cameras/preview', data={'csrf': csrf,
            'revision': site.aikey.snapshot().revision, 'mode': 'restrict', 'keep_camera': choice},
            headers={'Origin': site.origin})
        assert response.status == 409 and path.read_bytes() == before


@pytest.mark.parametrize('stage', ['previewed', 'admitted'])
async def test_session_expiring_during_sync_audit_cannot_grant_or_save(tmp_path, monkeypatch, stage):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        fields = (await preview(site, client, cookie) if stage == 'admitted' else
                  {'csrf': site.security.csrf_token(cookie), 'revision': site.aikey.snapshot().revision,
                   'mode': 'disable_all'})
        original = site.audit.record
        def late(action, result, **kwargs):
            saved = original(action, result, **kwargs)
            if result == stage:
                site.security.revoke(cookie)
            return saved
        monkeypatch.setattr(site.audit, 'record', late)
        before = path.read_bytes()
        response = (await apply(site, client, fields) if stage == 'admitted' else
                    await client.post('/key-face-cameras/preview', data=fields, headers={'Origin': site.origin}))
        assert response.status == 403 and path.read_bytes() == before
        if stage == 'previewed':
            assert not control.grants


async def test_live_grant_limit_preserves_old_grants_and_expiry_reopens_capacity(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        control.clock = lambda: 100
        control.grants = {str(i): {'deadline': 101} for i in range(32)}
        data = {'csrf': site.security.csrf_token(cookie), 'revision': site.aikey.snapshot().revision,
                'mode': 'disable_all'}
        before = path.read_bytes()
        response = await client.post('/key-face-cameras/preview', data=data, headers={'Origin': site.origin})
        assert response.status == 503 and len(control.grants) == 32
        control.clock = lambda: 102
        response = await client.post('/key-face-cameras/preview', data=data, headers={'Origin': site.origin})
        assert response.status == 200 and len(control.grants) == 1 and path.read_bytes() == before


async def test_shared_mutation_limit_blocks_save_but_keeps_reading_available(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        fields = await preview(site, client, cookie)
        site._mutation_times.extend([site._mutation_clock()] * 60)
        before = path.read_bytes()
        response = await apply(site, client, fields)
        assert response.status == 429 and 'Retry-After' in response.headers
        assert path.read_bytes() == before and (await client.get('/key-face-cameras')).status == 200


async def test_empty_policy_and_expired_session_are_not_live_worker_claims(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        raw = json.loads(path.read_bytes())
        del raw['face_recognition']
        path.write_text(json.dumps(raw))
        body = await (await client.get('/key-face-cameras')).text()
        assert 'No AI Key face recognition policy is saved' in body and 'live worker' in body
        assert '<form' not in body
        value = await (await client.get('/api/admin/v1/key-face-cameras')).json()
        assert not value['face_scope']['configured'] and value['face_scope']['explicit_camera_count'] == 0
        assert value['observation'] == 'saved_policy_not_running_authority'
        site.security.revoke(cookie)
        assert (await client.get('/key-face-cameras', allow_redirects=False)).status == 303
        assert (await client.get('/api/admin/v1/key-face-cameras')).status == 401


@pytest.mark.parametrize('fault', ['invalid_policy', 'duplicate_json'])
async def test_ambiguous_or_invalid_policy_is_unavailable_not_empty_or_editable(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        value = json.loads(path.read_bytes())
        if fault == 'invalid_policy':
            value['face_recognition']['server'] = 'https://foreign.invalid'
            path.write_text(json.dumps(value))
        else:
            path.write_text(json.dumps(value)[:-1] + ',"face_recognition":'
                            + json.dumps(value['face_recognition']) + '}')
        before = path.read_bytes()
        response = await client.get('/key-face-cameras')
        body = await response.text()
        assert response.status == 503 and 'scope unknown' in body and '<form' not in body
        assert 'foreign.invalid' not in body
        response = await client.get('/api/admin/v1/key-face-cameras')
        assert response.status == 503 and 'explicit_camera_count' not in await response.text()
        assert path.read_bytes() == before


async def test_timed_out_or_cancelled_reads_keep_one_owned_thread_until_completion(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, path, control, report, _):
        original = site.aikey.face_scope_status
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked():
            calls.append(True)
            entered.set()
            assert release.wait(8)
            return original()
        monkeypatch.setattr(site.aikey, 'face_scope_status', blocked)
        first = asyncio.create_task(control._status())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            with pytest.raises(TimeoutError):
                await first
            second = asyncio.create_task(control._status())
            await asyncio.sleep(0)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            assert len(calls) == 1 and len(site.admin_api.operations) == 1
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(control.read_task), 3)
            await site.admin_api.drain(None)
        assert len(calls) == 1 and not site.admin_api.operations

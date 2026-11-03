"""Authenticated complete controller review paths, including lost writes/ABA."""
import asyncio
import json
import re
import threading

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_audit import AuditLog
from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite, _COOKIE
from aikey import device_workflow as workflow_module
from test_device_workflow import setup, revision

PREFIX = '/api/admin/v1/device-workflows'
PASSWORD = 'synthetic-controller-admin'


def new_session(site):
    return site.security.login('fixture', PASSWORD, site.password_record, site.origin).credentials


@pytest.fixture
async def live(tmp_path):
    paths, selections = {}, {}
    for kind in ('aikey', 'aiport'):
        root = tmp_path / kind
        root.mkdir(mode=0o700)
        _, paths[kind], selections[kind] = setup(root, kind)
    site = ControlSite(paths['aikey'], aiport_instances={'front_door': paths['aiport']},
        signing_key=b'w' * 32, password_record=AdminSecurity.create_password_record(PASSWORD), port=8765,
        audit_log=tmp_path / 'audit.jsonl')
    server = TestServer(site.app(), host='127.0.0.1')
    await server.start_server()
    site.origin = f'http://127.0.0.1:{server.port}'
    site.security = AdminSecurity(b'w' * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    credentials = new_session(site)
    client.session.cookie_jar.update_cookies({_COOKIE: credentials.cookie}, response_url=URL(site.origin))
    headers = {'Origin': site.origin, 'X-CSRF-Token': credentials.csrf_token}
    try:
        yield site, client, credentials, headers, paths, selections
    finally:
        await client.close()


def payload(paths, selections, kind='aikey'):
    return {'profile': 'aikey' if kind == 'aikey' else 'aiport:front_door',
            'revision': revision(paths[kind]), 'selection': selections[kind]}


async def preview(live, kind='aikey'):
    _, client, _, headers, paths, selections = live
    response = await client.post(PREFIX + '/controller-preview', json=payload(paths, selections, kind), headers=headers)
    assert response.status == 200, await response.text()
    return await response.json()


def confirmation(review):
    return {'profile': review['review']['profile'], 'review_token': review['review_token'],
            'acknowledgements': review['review']['required_consents']}


@pytest.mark.parametrize('kind', ['aikey', 'aiport'])
async def test_preview_apply_exact_rollback_and_no_identity_or_other_profile_change(live, kind):
    site, client, _, headers, paths, selections = live
    before = {name: path.read_bytes() for name, path in paths.items()}
    identities = {(name, leaf): (path.parent / leaf).read_bytes()
                  for name, path in paths.items() for leaf in ('device.crt', 'device.key', 'identity.json')}
    review = await preview(live, kind)
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    assert selections[kind]['certificate_pem'] not in json.dumps(review)
    result = await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)
    assert result.status == 200, await result.text()
    saved = await result.json()
    assert saved['outcome'] == 'saved_and_read_back' and saved['completion_audit_recorded']
    assert saved['activation'] == 'saved_not_active_runtime'
    assert paths['aiport' if kind == 'aikey' else 'aikey'].read_bytes() == before['aiport' if kind == 'aikey' else 'aikey']
    rollback = await client.post(PREFIX + '/rollback-preview', json={
        'profile': review['review']['profile'], 'revision': revision(paths[kind])}, headers=headers)
    assert rollback.status == 200, await rollback.text()
    result = await client.post(PREFIX + '/apply', json=confirmation(await rollback.json()), headers=headers)
    assert result.status == 200, await result.text()
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    assert identities == {(name, leaf): (path.parent / leaf).read_bytes()
                          for name, path in paths.items() for leaf in ('device.crt', 'device.key', 'identity.json')}
    log = site.audit.recent()
    assert any(row.get('profile') == review['review']['profile'] and row['result'] == 'read_back' for row in log)
    assert 'unknown' not in {row.get('profile') for row in log}


async def test_identifiable_invalid_confirmation_consumes_siblings_and_exact_undo_cannot_revive_them(live):
    _, client, _, headers, paths, _ = live
    first, second = await preview(live), await preview(live)
    raw = paths['aikey'].read_bytes()
    bad = confirmation(first)
    bad['acknowledgements'] = []
    assert (await client.post(PREFIX + '/apply', json=bad, headers=headers)).status == 409
    assert (await client.post(PREFIX + '/apply', json=confirmation(second), headers=headers)).status == 409
    assert paths['aikey'].read_bytes() == raw
    stale, current = await preview(live), await preview(live)
    assert (await client.post(PREFIX + '/apply', json=confirmation(current), headers=headers)).status == 200
    undo = await client.post(PREFIX + '/rollback-preview', json={'profile': 'aikey', 'revision': revision(paths['aikey'])}, headers=headers)
    assert (await client.post(PREFIX + '/apply', json=confirmation(await undo.json()), headers=headers)).status == 200
    assert paths['aikey'].read_bytes() == raw
    assert (await client.post(PREFIX + '/apply', json=confirmation(stale), headers=headers)).status == 409


async def test_no_csrf_cross_session_wrong_profile_and_extra_fields(live):
    site, client, credentials, headers, paths, _ = live
    good = await preview(live)
    before = paths['aikey'].read_bytes()
    assert (await client.post(PREFIX + '/apply', json=confirmation(good), headers={'Origin': site.origin})).status == 403
    second = new_session(site)
    client.session.cookie_jar.update_cookies({_COOKIE: second.cookie}, response_url=URL(site.origin))
    assert (await client.post(PREFIX + '/apply', json=confirmation(good), headers={**headers, 'X-CSRF-Token': second.csrf_token})).status == 409
    client.session.cookie_jar.update_cookies({_COOKIE: credentials.cookie}, response_url=URL(site.origin))
    wrong = dict(confirmation(good), profile='aiport:front_door')
    assert (await client.post(PREFIX + '/apply', json=wrong, headers=headers)).status == 409
    assert (await client.post(PREFIX + '/apply', json=confirmation(good), headers=headers)).status == 409
    another = await preview(live)
    assert (await client.post(PREFIX + '/apply', json=dict(confirmation(another), path='/untrusted'), headers=headers)).status == 409
    assert paths['aikey'].read_bytes() == before


@pytest.mark.parametrize('fault', ['expired', 'session_revoked', 'audit_unavailable', 'audit_write_failed'])
async def test_expired_or_unaudited_authority_never_publishes(live, fault, monkeypatch):
    site, client, credentials, headers, paths, _ = live
    review = await preview(live)
    controls = site.admin_api.device_controls
    before = paths['aikey'].read_bytes()
    if fault == 'expired':
        controls.clock = lambda: 1e20
    elif fault == 'session_revoked':
        site.security.revoke(credentials.cookie)
    elif fault == 'audit_unavailable':
        site.audit = None
    else:
        monkeypatch.setattr(site.audit, 'record', lambda *args, **kwargs: False)
    result = await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)
    assert result.status in (403, 409)
    assert paths['aikey'].read_bytes() == before
    assert not (paths['aikey'].parent / 'controller-trust').exists()


async def test_confirmation_expires_during_validation_before_any_publication(live, monkeypatch):
    site, client, _, headers, paths, _ = live
    review = await preview(live)
    controls = site.admin_api.device_controls
    store = controls.stores['aikey']
    original = store._prepare_controller
    def delayed(*args):
        result = original(*args)
        controls.clock = lambda: 1e20
        return result
    monkeypatch.setattr(store, '_prepare_controller', delayed)
    before = paths['aikey'].read_bytes()
    assert (await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)).status == 409
    assert paths['aikey'].read_bytes() == before
    assert not (paths['aikey'].parent / 'controller-trust').exists()


@pytest.mark.parametrize('boundary',['before_admission','after_admission'])
async def test_disconnect_cannot_cancel_transaction_but_revocation_before_admission_refuses(live,monkeypatch,boundary):
    site,client,credentials,headers,paths,_=live
    review=await preview(live)
    entered,release=threading.Event(),threading.Event()
    store=site.admin_api.device_controls.stores['aikey']
    original=store._admit
    def paused(admission):
        if boundary=='after_admission':
            original(admission)
        entered.set()
        assert release.wait(5)
        if boundary=='before_admission':
            original(admission)
    monkeypatch.setattr(store,'_admit',paused)
    before=paths['aikey'].read_bytes()
    waiter=asyncio.create_task(client.post(PREFIX+'/apply',json=confirmation(review),headers=headers))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        assert site.security.revoke(credentials.cookie)
        waiter.cancel()
        await asyncio.gather(waiter,return_exceptions=True)
    finally:
        release.set()
    await asyncio.wait_for(site.admin_api.drain(None),5)
    if boundary=='before_admission':
        assert paths['aikey'].read_bytes()==before
        assert not (paths['aikey'].parent/'controller-trust').exists()
    else:
        assert revision(paths['aikey'])==review['review']['resulting_revision']
        assert store.transaction_status()['rollback_available']
    assert not site.admin_api.operations


async def test_uncertain_publication_reconciles_without_rewrite_or_retry(live, monkeypatch):
    _, client, _, headers, paths, _ = live
    review = await preview(live)
    original = workflow_module.atomic_private
    def lost_ack(path, raw):
        original(path, raw)
        if path == paths['aikey']:
            raise OSError('synthetic post-publication acknowledgment loss')
    monkeypatch.setattr(workflow_module, 'atomic_private', lost_ack)
    result = await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)
    assert result.status == 503 and (await result.json())['automatic_retry'] is False
    saved = paths['aikey'].read_bytes()
    refused = await client.post(PREFIX + '/controller-preview', json={**payload(paths, live[-1])}, headers=headers)
    assert refused.status == 409
    reconciliation = await client.post(PREFIX + '/reconcile-preview', json={'profile': 'aikey',
        'revision': revision(paths['aikey'])}, headers=headers)
    assert reconciliation.status == 200
    result = await client.post(PREFIX + '/apply', json=confirmation(await reconciliation.json()), headers=headers)
    assert result.status == 200
    assert (await result.json())['configuration_rewritten'] is False and paths['aikey'].read_bytes() == saved


async def test_completion_audit_loss_reports_saved_bytes_not_failed_write(live, monkeypatch):
    site, client, _, headers, paths, _ = live
    review = await preview(live)
    original = site.audit.record
    def no_completion(action, result, **kwargs):
        return False if result == 'read_back' else original(action, result, **kwargs)
    monkeypatch.setattr(site.audit, 'record', no_completion)
    result = await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)
    assert result.status == 200
    saved = await result.json()
    assert saved['outcome'] == 'saved_and_read_back' and saved['completion_audit_recorded'] is False
    assert saved['automatic_retry'] is False and revision(paths['aikey']) == review['review']['resulting_revision']


@pytest.mark.parametrize('expire', [False, True])
async def test_explicit_probe_consumes_old_authority_and_cannot_renew_expired_lease(live, expire):
    site, client, _, headers, paths, _ = live
    controls = site.admin_api.device_controls
    calls = []
    async def fake_probe(**kwargs):
        assert kwargs['admission']()
        calls.append(kwargs)
        if expire:
            controls.clock = lambda: 1e20
        return {'matched': True, 'device_credentials_sent': False, 'http_requests_sent': 0}
    controls.probe_runner = fake_probe
    review = await preview(live)
    before = paths['aikey'].read_bytes()
    request = {key: confirmation(review)[key] for key in ('profile', 'review_token')}
    result = await client.post(PREFIX + '/probe', json=request, headers=headers)
    assert result.status == (409 if expire else 200)
    if not expire:
        tested = await result.json()
        assert tested['review_token'] != review['review_token'] and tested['probe']['matched']
    assert len(calls) == 1 and paths['aikey'].read_bytes() == before
    assert (await client.post(PREFIX + '/apply', json=confirmation(review), headers=headers)).status == 409


async def test_native_form_review_has_only_opaque_authority_and_save_clears_fields(live, caplog):
    site, client, credentials, headers, paths, selections = live
    body = await (await client.get('/devices')).text()
    assert "<textarea name='certificate_pem'" in body and '<script' not in body
    form = dict(selections['aikey'], profile='aikey', revision=revision(paths['aikey']),
                csrf=credentials.csrf_token, verify_hostname='yes')
    response = await client.post('/devices/controller-preview', data=form, headers=headers)
    assert response.status == 200, await response.text()
    html = await response.text()
    assert 'BEGIN CERTIFICATE' not in html and selections['aikey']['certificate_pem'] not in html
    assert 'device.key' not in html and 'value=\'yes\' required checked' not in html
    token = re.search("name='review_token' value='([0-9a-f]{64})'", html)[1]
    assert len(re.findall("name='review_token'", html)) == 2
    result = await client.post('/devices/apply', data={'csrf': credentials.csrf_token, 'profile': 'aikey',
        'review_token': token, 'controller_destination_and_pin_reviewed': 'yes'}, headers=headers, allow_redirects=False)
    assert result.status == 303 and result.headers['Location'] == '/devices?saved=1'
    html = await (await client.get(result.headers['Location'])).text()
    assert 'BEGIN CERTIFICATE' not in html and token not in html
    assert 'BEGIN CERTIFICATE' not in caplog.text
    assert 'BEGIN CERTIFICATE' not in site.audit.path.read_text()


async def test_ambiguous_json_duplicate_form_and_non_json_are_refused(live):
    _, client, credentials, headers, paths, _ = live
    before = paths['aikey'].read_bytes()
    response = await client.post(PREFIX + '/controller-preview', data='{"profile":"aikey","profile":"aiport"}',
        headers={**headers, 'Content-Type': 'application/json'})
    assert response.status == 409
    response = await client.post('/devices/controller-preview', data=[('csrf', credentials.csrf_token),
        ('profile', 'aikey'), ('profile', 'aiport')], headers=headers)
    assert response.status == 409
    assert paths['aikey'].read_bytes() == before


def test_profile_audit_preserves_actual_instance_names(tmp_path):
    log = AuditLog(tmp_path / 'audit.jsonl')
    assert log.record('device_configuration', 'admitted', profile='aiport:front_door')
    assert log.recent()[0]['profile'] == 'aiport:front_door'

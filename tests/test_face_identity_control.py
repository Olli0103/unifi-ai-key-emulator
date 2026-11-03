"""Owned loopback HTTP, fictional identities/vectors, audit enabled from setup."""

import asyncio
from contextlib import asynccontextmanager
import json
import threading

from multidict import MultiDict
import pytest

from aikey.admin_audit import AuditLog
from aikey.faces import FaceStore, FaceStoreError, FaceStoreOutcomeUncertain, FaceStoreRevisionConflict
from test_control_site_secrets import assert_private_page, signed_in
from test_face_store_boundary import TEMPLATE
from test_ocr_provider_http import form

TARGET = 'Fictional target <script> & "name"'
SIBLING = 'Fictional sibling'


@asynccontextmanager
async def fixture(root, *, seed=True):
    site, server, client, cookie, key = await signed_in(root)
    site.audit = AuditLog(root / 'fictional-audit.jsonl')
    control = site.admin_api.face_identity_control
    if seed:
        control.store.enroll(SIBLING, TEMPLATE, embedding_space='a' * 64)
        control.store.enroll(TARGET, TEMPLATE, embedding_space='a' * 64)
        control.store.enroll(TARGET, TEMPLATE, embedding_space='a' * 64)
    try:
        yield site, client, cookie, control, key
    finally:
        await client.close()
        await server.close()


async def preview(site, client):
    response = await client.get('/face-identities')
    assert response.status == 200
    body = await response.text()
    assert_private_page(response, body)
    fields = form(body, '/face-identities/preview')
    response = await client.post('/face-identities/preview', data=fields,
                                 headers={'Origin': site.origin})
    assert response.status == 200
    body = await response.text()
    assert_private_page(response, body)
    fields = form(body, '/face-identities/apply')
    fields['confirm'] = 'yes'
    return fields


async def apply(site, client, fields):
    return await client.post('/face-identities/apply', data=fields,
                              headers={'Origin': site.origin}, allow_redirects=False)


async def test_private_list_preview_and_targeted_delete_preserve_sibling_and_configuration(tmp_path, caplog):
    async with fixture(tmp_path) as (site, client, cookie, control, key):
        before = control.store.path.read_bytes()
        key_before = key.read_bytes()
        port_before = site.aiport.path.read_bytes()
        sibling = json.loads(before)['identities'][SIBLING]
        response = await client.get('/face-identities')
        body = await response.text()
        assert_private_page(response, body)
        assert '<script>' not in body and '&lt;script&gt; &amp; &quot;name&quot;' in body
        assert 'Saved templates: 2' in body and 'AI Port 512-dimensional' in body
        assert 'in-flight match' in body and 'backups' in body
        assert str(control.store.directory) not in body and '[1.0,' not in body
        assert "/face-identities'>Local face identities" in await (await client.get('/')).text()
        metadata_response = await client.get('/api/admin/v1/face-identities')
        metadata = await metadata_response.json()
        assert metadata['scope'] == 'local_aikey_sface128'
        assert metadata['live_worker_state'] == 'not_asserted'
        assert metadata['identities'][-1] == {'name': TARGET, 'templates': 2}
        assert 'created' not in json.dumps(metadata) and '[1.0,' not in json.dumps(metadata)
        fields = await preview(site, client)
        assert set(fields) == {'csrf', 'grant', 'confirm'}
        assert control.store.path.read_bytes() == before
        response = await apply(site, client, fields)
        assert response.status == 303 and response.headers['Location'] == '/face-identities'
        saved = json.loads(control.store.path.read_bytes())
        assert saved['identities'] == {SIBLING: sibling}
        assert key.read_bytes() == key_before and site.aiport.path.read_bytes() == port_before
        assert [r['result'] for r in reversed(site.audit.recent())] == ['previewed', 'admitted', 'saved']
        assert all(r['action'] == 'face_identity_delete' for r in site.audit.recent())
        assert TARGET not in site.audit.path.read_text() and TARGET not in caplog.text
        assert fields['grant'] not in caplog.text and fields['grant'] not in site.audit.path.read_text()
        assert not control.grants
        assert (await apply(site, client, fields)).status == 409


@pytest.mark.parametrize('fault', ['grant', 'confirm', 'extra', 'duplicate', 'csrf', 'origin',
                                  'host', 'expired_session', 'stale', 'expired_grant', 'query', 'method'])
async def test_invalid_confirmations_do_not_delete(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        origin = site.origin
        headers = {}
        path = '/face-identities/apply'
        if fault == 'grant':
            fields['grant'] = '0' * 64
        elif fault == 'confirm':
            fields['confirm'] = 'no'
        elif fault == 'extra':
            fields['name'] = SIBLING
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
        elif fault == 'stale':
            control.store.enroll('Fictional later identity', TEMPLATE, embedding_space='a' * 64)
        elif fault == 'expired_grant':
            control.clock = lambda: float('inf')
        elif fault == 'query':
            path += '?name=canary'
        elif fault == 'method':
            headers['Content-Type'] = 'application/json'
        before = control.store.path.read_bytes()
        response = await client.post(path, data=fields, headers={'Origin': origin, **headers},
                                     allow_redirects=False)
        assert response.status in {403, 409}
        assert control.store.path.read_bytes() == before
        assert 'saved' not in [r['result'] for r in site.audit.recent()]


@pytest.mark.parametrize('fault', ['revision', 'identity', 'duplicate', 'extra', 'query', 'csrf', 'stale'])
async def test_invalid_previews_mint_no_authority(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        path = '/face-identities/preview'
        if fault in {'revision', 'identity', 'csrf'}:
            fields[fault] = '0' * 64
        elif fault == 'duplicate':
            fields = MultiDict(fields)
            fields.add('identity', fields['identity'])
        elif fault == 'extra':
            fields['state_dir'] = '/fictional/other'
        elif fault == 'query':
            path += '?profile=aiport'
        else:
            control.store.enroll('Fictional later identity', TEMPLATE, embedding_space='a' * 64)
        before = control.store.path.read_bytes()
        response = await client.post(path, data=fields, headers={'Origin': site.origin})
        assert response.status in {403, 409} and not control.grants
        assert control.store.path.read_bytes() == before


async def test_choices_and_grants_cannot_cross_sessions(tmp_path):
    from test_control_site_secrets import PASSWORD
    from aikey.control_site import _COOKIE
    from yarl import URL
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        choices = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        fields = await preview(site, client)
        await client.post('/login', data={'password': PASSWORD}, headers={'Origin': site.origin})
        other = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        assert other != cookie
        choices['csrf'] = fields['csrf'] = site.security.csrf_token(other)
        before = control.store.path.read_bytes()
        response = await client.post('/face-identities/preview', data=choices, headers={'Origin': site.origin})
        assert response.status == 409
        assert (await apply(site, client, fields)).status == 409
        assert control.store.path.read_bytes() == before


async def test_sibling_previews_stay_consumed_after_exact_byte_restore(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        first, second = await preview(site, client), await preview(site, client)
        before = control.store.path.read_bytes()
        assert (await apply(site, client, first)).status == 303
        control.store.path.write_bytes(before)  # fixture restoration, not product undo
        assert (await apply(site, client, second)).status == 409
        assert control.store.path.read_bytes() == before


@pytest.mark.parametrize('fault', ['expiry', 'session', 'consumed'])
async def test_authority_is_rechecked_after_awaited_read(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        original = control._snapshot
        async def changed():
            result = await original()
            if fault == 'expiry':
                control.clock = lambda: float('inf')
            elif fault == 'session':
                site.security.revoke(cookie)
            else:
                control.grants.clear()
            return result
        monkeypatch.setattr(control, '_snapshot', changed)
        before = control.store.path.read_bytes()
        assert (await apply(site, client, fields)).status in {403, 409}
        assert control.store.path.read_bytes() == before


async def test_exactly_one_concurrent_admission_and_consumed_replay(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        first, second = await preview(site, client), await preview(site, client)
        original = control._snapshot
        arrived, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def paused():
            result = await original()
            calls.append(True)
            if len(calls) == 2:
                arrived.set()
            await release.wait()
            return result
        monkeypatch.setattr(control, '_snapshot', paused)
        a = asyncio.create_task(apply(site, client, first))
        b = asyncio.create_task(apply(site, client, second))
        await asyncio.wait_for(arrived.wait(), 3)
        release.set()
        results = await asyncio.gather(a, b)
        assert sorted(r.status for r in results) == [303, 409]
        assert control.store.names() == [SIBLING] and not control.grants
        assert [r['result'] for r in site.audit.recent()].count('admitted') == 1


@pytest.mark.parametrize('stage', ['previewed', 'admitted', 'saved'])
async def test_configured_audit_failure_is_not_silent_success(tmp_path, monkeypatch, stage):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        original = site.audit.record
        def failed(action, result, **kwargs):
            if result == stage:
                return False
            return original(action, result, **kwargs)
        if stage != 'previewed':
            fields = await preview(site, client)
        else:
            fields = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        monkeypatch.setattr(site.audit, 'record', failed)
        before = control.store.path.read_bytes()
        if stage == 'previewed':
            response = await client.post('/face-identities/preview', data=fields, headers={'Origin': site.origin})
            assert not control.grants
        else:
            response = await apply(site, client, fields)
        assert response.status == 503
        if stage == 'saved':
            assert control.store.names() == [SIBLING]
            assert 'outcome uncertain' in (await response.text()).lower()
            assert not control.grants
        else:
            assert control.store.path.read_bytes() == before


@pytest.mark.parametrize('fault', ['conflict', 'prewrite', 'uncertain_before', 'uncertain_after', 'false_ack'])
async def test_writer_boundary_distinguishes_refusal_from_possible_commit(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        before = control.store.path.read_bytes()
        original = control.store.delete
        def failed(name, **kwargs):
            if fault == 'conflict':
                raise FaceStoreRevisionConflict('fictional secret diagnostic')
            if fault == 'prewrite':
                raise FaceStoreError('fictional secret diagnostic')
            if fault == 'uncertain_after':
                original(name, **kwargs)
            if fault == 'false_ack':
                return False
            raise FaceStoreOutcomeUncertain('fictional secret diagnostic')
        monkeypatch.setattr(control.store, 'delete', failed)
        response = await apply(site, client, fields)
        assert response.status == (409 if fault in {'conflict', 'prewrite'} else 503)
        assert 'fictional secret diagnostic' not in await response.text()
        assert not control.grants
        if fault == 'uncertain_after':
            assert control.store.names() == [SIBLING]
        else:
            assert control.store.path.read_bytes() == before
        assert (await apply(site, client, fields)).status == 409


async def test_concurrent_external_enrollment_is_checked_inside_write_lock(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        original = control._commit
        async def raced(grant):
            FaceStore(site.aikey_state_dir).enroll('Fictional concurrent identity', TEMPLATE, embedding_space='a' * 64)
            return await original(grant)
        monkeypatch.setattr(control, '_commit', raced)
        assert (await apply(site, client, fields)).status == 409
        assert control.store.names() == ['Fictional concurrent identity', SIBLING, TARGET]


@pytest.mark.parametrize('path', ['/face-identities', '/api/admin/v1/face-identities'])
async def test_reads_require_authentication_and_never_create_missing_state(tmp_path, path):
    async with fixture(tmp_path, seed=False) as (site, client, cookie, control, _):
        missing = tmp_path / 'fictional-missing-state'
        control.store = FaceStore(missing)
        response = await client.get(path)
        assert response.status == 200 and not missing.exists()
        if path == '/face-identities':
            assert 'No local named templates' in await response.text()
        site.security.revoke(cookie)
        response = await client.get(path, allow_redirects=False)
        assert response.status in {303, 401} and not missing.exists()


@pytest.mark.parametrize('fault', ['corrupt', 'linked_directory', 'mode'])
async def test_unknown_or_unsafe_store_shows_no_count_or_choice(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        before = control.store.path.read_bytes()
        if fault == 'corrupt':
            control.store.path.write_text('CANARY-private-corrupt-vector')
        elif fault == 'linked_directory':
            original = control.store.directory
            renamed = original.with_name('fictional-outside-faces')
            original.rename(renamed)
            original.symlink_to(renamed, target_is_directory=True)
        else:
            control.store.directory.chmod(0o755)
        response = await client.get('/face-identities')
        body = await response.text()
        assert response.status == 503 and 'store unavailable' in body
        assert 'Saved templates:' not in body and '/face-identities/preview' not in body
        assert TARGET not in body and 'CANARY' not in body
        response = await client.get('/api/admin/v1/face-identities')
        assert response.status == 503 and 'CANARY' not in await response.text()
        if fault != 'corrupt':
            assert control.store.path.read_bytes() == before


async def test_limited_live_previews_are_not_evicted_and_expired_slots_are_reused(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        control.clock = lambda: 100
        control.grants = {str(i): {'deadline': 101} for i in range(32)}
        response = await client.post('/face-identities/preview', data=fields, headers={'Origin': site.origin})
        assert response.status == 503 and len(control.grants) == 32
        control.clock = lambda: 102
        response = await client.post('/face-identities/preview', data=fields, headers={'Origin': site.origin})
        assert response.status == 200 and len(control.grants) == 1


async def test_read_timeout_retains_one_thread_and_cleanup_observes_it(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        original = control.store.snapshot
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked():
            calls.append(True)
            entered.set()
            assert release.wait(8)
            return original()
        monkeypatch.setattr(control.store, 'snapshot', blocked)
        first = asyncio.create_task(control._snapshot())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            with pytest.raises(TimeoutError):
                await first
            assert len(site.admin_api.operations) == 1 and len(calls) == 1
            second = asyncio.create_task(control._snapshot())
            await asyncio.sleep(0)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            assert len(calls) == 1 and len(site.admin_api.operations) == 1
        finally:
            release.set()
            await site.admin_api.drain(None)
        assert control.read_task.done() and not site.admin_api.operations


async def test_waiter_cancellation_does_not_cancel_admitted_delete(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        original = control.store.delete
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(6)
            return original(*args, **kwargs)
        monkeypatch.setattr(control.store, 'delete', blocked)
        request = asyncio.create_task(apply(site, client, fields))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not control.grants
        finally:
            release.set()
            await site.admin_api.drain(None)
        assert control.store.names() == [SIBLING]
        assert 'saved' in [r['result'] for r in site.audit.recent()]


@pytest.mark.parametrize('stage', ['previewed', 'admitted'])
async def test_session_expiration_during_audit_never_grants_or_deletes(tmp_path, monkeypatch, stage):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        if stage == 'previewed':
            fields = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        else:
            fields = await preview(site, client)
        original = site.audit.record
        def late(action, result, **kwargs):
            recorded = original(action, result, **kwargs)
            if result == stage:
                site.security.revoke(cookie)
            return recorded
        monkeypatch.setattr(site.audit, 'record', late)
        before = control.store.path.read_bytes()
        if stage == 'previewed':
            response = await client.post('/face-identities/preview', data=fields, headers={'Origin': site.origin})
            assert not control.grants
        else:
            response = await apply(site, client, fields)
        assert response.status == 403 and control.store.path.read_bytes() == before


async def test_shared_mutation_limit_refuses_delete_without_blocking_reads(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        fields = await preview(site, client)
        now = site._mutation_clock()
        site._mutation_times.extend([now] * 60)
        before = control.store.path.read_bytes()
        response = await apply(site, client, fields)
        assert response.status == 429 and 'Retry-After' in response.headers
        assert control.store.path.read_bytes() == before
        assert (await client.get('/face-identities')).status == 200


async def test_optional_audit_policy_does_not_require_a_new_logger(tmp_path):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        site.audit = None
        fields = await preview(site, client)
        assert (await apply(site, client, fields)).status == 303
        assert control.store.names() == [SIBLING]


@pytest.mark.parametrize('stage', ['preview', 'apply'])
async def test_real_unwritable_audit_refuses_before_deletion_and_preserves_canary(tmp_path, stage):
    async with fixture(tmp_path) as (site, client, cookie, control, _):
        if stage == 'apply':
            fields = await preview(site, client)
            site.audit.path.rename(tmp_path / 'retained-fictional-audit.jsonl')
        else:
            fields = form(await (await client.get('/face-identities')).text(), '/face-identities/preview')
        canary = tmp_path / 'fictional-audit-canary'
        canary.write_text('retained unrelated synthetic bytes')
        site.audit.path.symlink_to(canary)
        before = control.store.path.read_bytes()
        if stage == 'apply':
            response = await apply(site, client, fields)
        else:
            response = await client.post('/face-identities/preview', data=fields, headers={'Origin': site.origin})
        assert response.status == 503 and site.audit.failures == 1
        assert control.store.path.read_bytes() == before
        assert canary.read_text() == 'retained unrelated synthetic bytes'

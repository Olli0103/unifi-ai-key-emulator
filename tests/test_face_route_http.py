"""Owned authenticated forms; no real service, profile or biometric record."""

import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest

from aikey.admin_audit import AuditLog
from aikey.config_store import FaceRouteOutcomeUncertain
from test_control_site_secrets import PASSWORD, assert_private_page, signed_in
from test_ocr_provider_http import form

OLD, NEW = 'http://127.0.0.1:8179', 'http://127.0.0.1:9191'


@asynccontextmanager
async def fixture(root, *, configured=True):
    site, server, client, cookie, key = await signed_in(root)
    try:
        raw = json.loads(key.read_text())
        if configured:
            raw['face_recognition'] = {'server': OLD, 'camera_ids': ['fictional-camera-b', 'fictional-camera-a'],
                                      'native_face_cameras': 'skip', 'max_faces': 3}
        raw['metadata'] = {'private': 'FICTIONAL-ROUTE-CANARY'}
        key.write_text(json.dumps(raw) + '\n')
        site.audit = AuditLog(root / 'route-audit.jsonl')
        yield site, client, cookie, site.admin_api.face_route_control, key
    finally:
        await client.close()
        await server.close()


async def preview(site, client, *, changes=None, headers=None):
    page = await client.get('/face-route')
    body = await page.text()
    assert page.status == 200 and 'FICTIONAL-ROUTE-CANARY' not in body
    assert_private_page(page, body)
    fields = form(body, '/face-route/preview')
    fields['server'] = NEW
    fields.update(changes or {})
    return await client.post('/face-route/preview', data=fields,
        headers={'Origin': site.origin, **(headers or {})}, allow_redirects=False)


async def grant(site, client):
    response = await preview(site, client)
    body = await response.text()
    assert response.status == 200, body
    assert 'New local destination' in body and NEW in body
    assert 'checked' not in body and 'camera-a' not in body and 'FICTIONAL-ROUTE-CANARY' not in body
    assert_private_page(response, body)
    fields = form(body, '/face-route/apply')
    fields['confirm'] = 'yes'
    return fields


async def apply(site, client, fields, *, headers=None):
    return await client.post('/face-route/apply', data=fields,
        headers={'Origin': site.origin, **(headers or {})}, allow_redirects=False)


async def test_separate_preview_confirmation_changes_only_route_and_logs_no_private_fields(tmp_path, caplog):
    async with fixture(tmp_path) as (site, client, _, control, key):
        before = key.read_bytes()
        fields = await grant(site, client)
        assert key.read_bytes() == before and not site.aikey.history_dir.exists()
        response = await apply(site, client, fields)
        body = await response.text()
        assert response.status == 200 and 'Local face route saved' in body
        expected = json.loads(before)
        expected['face_recognition']['server'] = NEW
        assert json.loads(key.read_bytes()) == expected and not control.grants
        assert [r['result'] for r in reversed(site.audit.recent())] == ['previewed', 'admitted', 'saved']
        audit = site.audit.path.read_text() + caplog.text
        assert OLD not in audit and NEW not in audit and fields['grant'] not in audit
        assert 'FICTIONAL-ROUTE-CANARY' not in audit and 'fictional-camera' not in audit
        assert_private_page(response, body)
        assert (await apply(site, client, fields)).status == 409


@pytest.mark.parametrize('changes,headers', [
    ({'csrf': 'wrong'}, {}), ({'revision': '0' * 64}, {}), ({'camera_ids': 'fictional-new'}, {}),
    ({'server': 'http://169.254.169.254'}, {}), ({'server': 'http://user:pass@127.0.0.1'}, {}),
    ({'server': 'http://public.invalid'}, {}), ({'server': 'x' * 513}, {}),
    ({}, {'Origin': 'https://fictional-other.invalid'}), ({}, {'Host': 'fictional-other.invalid'}),
])
async def test_bad_preview_cannot_change_scope_or_contact_a_service(tmp_path, changes, headers):
    async with fixture(tmp_path) as (site, client, _, control, key):
        before = key.read_bytes()
        response = await preview(site, client, changes=changes, headers=headers)
        assert response.status in (403, 409)
        assert key.read_bytes() == before and not control.grants and not site.aikey.history_dir.exists()


@pytest.mark.parametrize('fault', ['missing_confirm', 'wrong_confirm', 'forged_server', 'duplicate', 'origin'])
async def test_bad_confirmation_never_changes_state_or_consumes_valid_grant(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, _, control, key):
        fields = await grant(site, client)
        before = key.read_bytes()
        headers = {}
        if fault == 'missing_confirm':
            fields.pop('confirm')
        elif fault == 'wrong_confirm':
            fields['confirm'] = 'no'
        elif fault == 'forged_server':
            fields['server'] = 'http://127.0.0.1:9999'
        elif fault == 'duplicate':
            fields = list(fields.items()) + [('confirm', 'yes')]
        else:
            headers['Origin'] = 'https://fictional-other.invalid'
        response = await apply(site, client, fields, headers=headers)
        assert response.status in (403, 409)
        assert key.read_bytes() == before and len(control.grants) == 1


@pytest.mark.parametrize('fault', ['expiry', 'configuration', 'logout', 'cleanup'])
async def test_expired_stale_revoked_or_closed_confirmation_cannot_save(tmp_path, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, key):
        fields = await grant(site, client)
        if fault == 'expiry':
            control.clock = lambda: 1e20
        elif fault == 'configuration':
            key.write_bytes(key.read_bytes() + b'\n')
        elif fault == 'logout':
            site.security.revoke(cookie)
        else:
            await control.cleanup(None)
        before = key.read_bytes()
        assert (await apply(site, client, fields)).status in (403, 409)
        assert key.read_bytes() == before


@pytest.mark.parametrize('phase', ['previewed', 'admitted'])
async def test_configured_audit_failure_refuses_before_write(tmp_path, monkeypatch, phase):
    async with fixture(tmp_path) as (site, client, _, control, key):
        before = key.read_bytes()
        audit = control._audit
        monkeypatch.setattr(control, '_audit', lambda a: False if a == phase else audit(a))
        if phase == 'previewed':
            response = await preview(site, client)
        else:
            response = await apply(site, client, await grant(site, client))
        assert response.status == 503 and key.read_bytes() == before
        assert not site.aikey.history_dir.exists()


@pytest.mark.parametrize('fault', ['revision', 'session', 'deadline'])
async def test_last_moment_authority_changes_are_rechecked_after_audit(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path) as (site, client, cookie, control, key):
        fields = await grant(site, client)
        audit = control._audit
        def change(action):
            if action == 'admitted':
                if fault == 'revision':
                    key.write_bytes(key.read_bytes() + b'\n')
                elif fault == 'session':
                    site.security.revoke(cookie)
                else:
                    control.clock = lambda: 1e20
            return audit(action)
        monkeypatch.setattr(control, '_audit', change)
        assert (await apply(site, client, fields)).status in (403, 409)
        assert json.loads(key.read_bytes())['face_recognition']['server'] == OLD
        assert not site.aikey.history_dir.exists()


@pytest.mark.parametrize('fault', ['write_uncertain', 'saved_audit'])
async def test_uncertain_commit_consumes_siblings_without_retry_or_restore(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path) as (site, client, _, control, key):
        first, sibling = await grant(site, client), await grant(site, client)
        if fault == 'write_uncertain':
            original = site.aikey.apply_face_route
            def uncertain(*args):
                original(*args)
                raise FaceRouteOutcomeUncertain('fictional lost acknowledgement')
            monkeypatch.setattr(site.aikey, 'apply_face_route', uncertain)
        else:
            audit = control._audit
            monkeypatch.setattr(control, '_audit', lambda a: False if a == 'saved' else audit(a))
        response = await apply(site, client, first)
        assert response.status == 503 and 'outcome uncertain' in (await response.text()).lower()
        assert json.loads(key.read_bytes())['face_recognition']['server'] == NEW
        assert not control.grants
        assert (await apply(site, client, first)).status == (await apply(site, client, sibling)).status == 409


async def test_grant_cannot_move_to_a_different_authenticated_session(tmp_path):
    async with fixture(tmp_path) as (site, client, _, control, key):
        fields = await grant(site, client)
        before = key.read_bytes()
        await client.post('/login', data={'password': PASSWORD}, headers={'Origin': site.origin},
                          allow_redirects=False)
        from aikey.control_site import _COOKIE
        from yarl import URL
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        fields['csrf'] = site.security.csrf_token(cookie)
        assert (await apply(site, client, fields)).status == 409
        assert key.read_bytes() == before and len(control.grants) == 1


async def test_success_invalidates_pending_enrollment_vectors_not_saved_templates(tmp_path):
    async with fixture(tmp_path) as (site, client, _, control, key):
        vector = [1.0] + [0.0] * 127
        timer = SimpleNamespace(cancel=lambda: None)
        site.admin_api.face_enrollment_control.grants['fictional'] = {'template': vector, 'timer': timer}
        face = site.admin_api.face_identity_control.store
        face.directory.mkdir(mode=0o700)
        face.path.write_bytes(b'fictional-invalid-template-must-not-be-read')
        face.path.chmod(0o600)
        fields = await grant(site, client)
        assert (await apply(site, client, fields)).status == 200
        assert not site.admin_api.face_enrollment_control.grants and vector == []
        assert face.path.read_bytes() == b'fictional-invalid-template-must-not-be-read'


async def test_unconfigured_role_offers_no_form_and_cannot_create_policy(tmp_path):
    async with fixture(tmp_path, configured=False) as (site, client, cookie, control, key):
        before = key.read_bytes()
        page = await client.get('/face-route')
        assert page.status == 200 and '<form' not in await page.text()
        fields = {'csrf': site.security.csrf_token(cookie), 'revision': site.aikey.snapshot().revision,
                  'server': NEW}
        response = await client.post('/face-route/preview', data=fields, headers={'Origin': site.origin})
        assert response.status == 409 and not control.grants and key.read_bytes() == before


async def test_two_concurrent_confirmations_have_one_winner(tmp_path):
    async with fixture(tmp_path) as (site, client, _, control, key):
        fields = await grant(site, client)
        a, b = await asyncio.gather(apply(site, client, fields), apply(site, client, fields))
        assert sorted([a.status, b.status]) == [200, 409]
        assert json.loads(key.read_bytes())['face_recognition']['server'] == NEW
        assert not control.grants and len(list(site.aikey.history_dir.iterdir())) == 1


async def test_mutation_limit_refuses_before_preview_or_commit(tmp_path, monkeypatch):
    from test_admin_mutation_limit import fill
    async with fixture(tmp_path) as (site, client, cookie, control, key):
        fields = await grant(site, client)
        before = key.read_bytes()
        site._mutation_times.clear()
        await fill(site, client, cookie)
        def forbidden(*args):
            pytest.fail('Throttled route must not enter file work')
        monkeypatch.setattr(site.aikey, 'preview_face_route', forbidden)
        response = await apply(site, client, fields)
        assert response.status == 429 and key.read_bytes() == before
        assert fields['grant'] in control.grants


async def test_pending_forms_are_bounded_and_expiry_releases_capacity(tmp_path):
    async with fixture(tmp_path) as (site, client, _, control, key):
        before = key.read_bytes()
        for _ in range(16):
            await grant(site, client)
        assert len(control.grants) == 16
        response = await preview(site, client)
        assert response.status == 409 and key.read_bytes() == before
        control.clock = lambda: 1e20
        assert (await preview(site, client)).status == 200
        assert len(control.grants) == 1 and key.read_bytes() == before


async def test_noop_has_no_confirmation_or_history(tmp_path):
    async with fixture(tmp_path) as (site, client, _, control, key):
        before = key.read_bytes()
        response = await preview(site, client, changes={'server': OLD + '/'})
        assert response.status == 200 and 'Local face route unchanged' in await response.text()
        assert key.read_bytes() == before and not control.grants and not site.aikey.history_dir.exists()


async def test_cancelled_waiter_does_not_abandon_owned_write_or_cleanup(tmp_path, monkeypatch):
    import threading
    async with fixture(tmp_path) as (site, client, _, control, key):
        fields = await grant(site, client)
        value = dict(control.grants[fields['grant']])
        original = site.aikey.apply_face_route
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            assert release.wait(timeout=5)
            return original(*args)
        monkeypatch.setattr(site.aikey, 'apply_face_route', blocked)
        task = asyncio.create_task(site.admin_api.file_operation(control._commit, value))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            drain = asyncio.create_task(site.admin_api.drain(None))
            await asyncio.sleep(0)
            assert not drain.done() and site.admin_api.operations
            release.set()
            await asyncio.wait_for(drain, 3)
            assert json.loads(key.read_bytes())['face_recognition']['server'] == NEW
            assert not site.admin_api.operations
        finally:
            release.set()

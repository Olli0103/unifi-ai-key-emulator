"""Owned HTTP/fictional JPEG bytes and vectors; never real images or model calls."""

from contextlib import asynccontextmanager
import json

from aiohttp import FormData
import pytest

from aikey import face_enrollment_control
from aikey.admin_audit import AuditLog
from aikey.faces import FaceStore
from test_control_site_secrets import assert_private_page, signed_in
from test_ocr_provider_http import form

SPACE = 'a' * 64
VECTOR = [1.0] + [0.0] * 127
IMAGE = b'\xff\xd8\xff' + b'FICTIONAL-ENROLLMENT' * 8
NAME = 'Fictional <script> identity & "label"'


@asynccontextmanager
async def fixture(root, monkeypatch, *, configured=True):
    site, server, client, cookie, key = await signed_in(root)
    try:
        raw = json.loads(key.read_text())
        if configured:
            raw['face_recognition'] = {'server': 'http://127.0.0.1:8179',
                                       'camera_ids': ['fictional-face-camera']}
        key.write_text(json.dumps(raw) + '\n')
        site.audit = AuditLog(root / 'enrollment-audit.jsonl')
        control = site.admin_api.face_enrollment_control
        calls = []
        def embed(image, endpoint):
            calls.append((image, endpoint))
            return list(VECTOR), SPACE
        monkeypatch.setattr(face_enrollment_control, 'embed_single_face', embed)
        yield site, client, cookie, control, key, calls
    finally:
        await client.close()
        await server.close()



async def upload(site, client, *, changes=None, image=IMAGE, extra=None, headers=None):
    page = await client.get('/face-identities/enroll')
    body = await page.text()
    assert page.status == 200, body
    assert_private_page(page, body)
    values = form(body, '/face-identities/enroll/preview')
    values.pop('image', None)
    values.update({'name': NAME, 'consent': 'yes'})
    values.update(changes or {})
    data = FormData()
    for name, value in values.items():
        if value is not None:
            data.add_field(name, value)
    data.add_field('image', image, filename='fictional.jpg', content_type='image/jpeg')
    for name, value in (extra or []):
        if isinstance(value, bytes):
            data.add_field(name, value, filename='fictional-duplicate.jpg', content_type='image/jpeg')
        else:
            data.add_field(name, value)
    return await client.post('/face-identities/enroll/preview', data=data,
                             headers={'Origin': site.origin, **(headers or {})}, allow_redirects=False)


async def preview(site, client):
    response = await upload(site, client)
    body = await response.text()
    assert response.status == 200, body
    assert_private_page(response, body)
    assert '&lt;script&gt;' in body and 'Nothing has been enrolled yet' in body
    assert 'http://127.0.0.1:8179' not in body and SPACE not in body
    assert '<input type=\'checkbox\' name=\'confirm\'' in body and 'checked' not in body
    fields = form(body, '/face-identities/enroll/apply')
    fields['confirm'] = 'yes'
    return fields


async def apply(site, client, fields):
    return await client.post('/face-identities/enroll/apply', data=fields,
                             headers={'Origin': site.origin}, allow_redirects=False)


async def test_explicit_analysis_then_separate_save_retains_binding_and_no_configuration_changes(tmp_path, monkeypatch, caplog):
    async with fixture(tmp_path, monkeypatch) as (site, client, cookie, control, key, calls):
        before = key.read_bytes()
        fields = await preview(site, client)
        assert calls == [(IMAGE, 'http://127.0.0.1:8179')]
        assert not control.store.path.exists()
        assert key.read_bytes() == before
        response = await apply(site, client, fields)
        assert response.status == 303
        assert FaceStore(key.parent).match(VECTOR, embedding_space=SPACE)[0] == NAME
        assert key.read_bytes() == before and not control.grants
        assert [r['result'] for r in reversed(site.audit.recent())] == ['analysis_requested', 'previewed', 'admitted', 'saved']
        text = site.audit.path.read_text() + caplog.text
        assert NAME not in text and fields['grant'] not in text and 'FICTIONAL-ENROLLMENT' not in text
        assert (await apply(site, client, fields)).status == 409
        assert len(calls) == 1


@pytest.mark.parametrize('changes,extra', [
    ({'consent': None}, None), ({'consent': 'no'}, None), ({'csrf': 'wrong'}, None),
    ({'name': '\n'}, None), ({'revision': '0' * 64}, None),
    ({'config_revision': '0' * 64}, None), ({}, [('server', 'http://127.0.0.1:9999')]),
    ({}, [('consent', 'yes')]),
])
async def test_invalid_or_missing_consent_input_never_calls_model_or_saves(tmp_path, monkeypatch, changes, extra):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        response = await upload(site, client, changes=changes, extra=extra)
        assert response.status in (403, 409)
        assert calls == [] and not control.store.path.exists() and not control.grants
        assert not control.busy


async def test_cancel_discards_transient_vector_without_any_store_write(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        fields = await preview(site, client)
        vector = control.grants[fields['grant']]['template']
        fields.pop('confirm')
        response = await client.post('/face-identities/enroll/cancel', data=fields,
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303 and vector == [] and not control.grants
        assert len(calls) == 1 and not control.store.path.exists()


async def test_absent_role_does_not_offer_upload_or_create_permission(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch, configured=False) as (site, client, _, control, key, calls):
        before = key.read_bytes()
        response = await client.get('/face-identities/enroll')
        assert response.status == 503 and '<form' not in await response.text()
        assert calls == [] and not control.grants and key.read_bytes() == before


async def test_legacy_record_remains_metadata_deletion_only_without_analysis(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (_, client, _, control, _, calls):
        control.store.directory.mkdir(mode=0o700)
        control.store.path.write_text(json.dumps({'schema': 1, 'identities': {
            NAME: {'templates': [VECTOR], 'created': 1}}}))
        control.store.path.chmod(0o600)
        before = control.store.path.read_bytes()
        response = await client.get('/face-identities/enroll')
        assert response.status == 503 and '<form' not in await response.text()
        assert calls == [] and control.store.path.read_bytes() == before
        assert (await client.get('/face-identities')).status == 200


@pytest.mark.parametrize('fault', ['store_changed', 'config_changed', 'expired', 'no_confirm', 'audit'])
async def test_invalidated_preview_never_saves_or_repeats_analysis(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, key, calls):
        fields = await preview(site, client)
        if fault == 'store_changed':
            control.store.enroll('Fictional concurrent identity', VECTOR, embedding_space=SPACE)
        elif fault == 'config_changed':
            key.write_bytes(key.read_bytes() + b'\n')
        elif fault == 'expired':
            deadline = control.clock() + 121
            control.clock = lambda: deadline
        elif fault == 'no_confirm':
            fields['confirm'] = 'no'
        else:
            monkeypatch.setattr(control, '_audit', lambda _: False)
        before = control.store.path.read_bytes() if control.store.path.exists() else None
        assert (await apply(site, client, fields)).status in (409, 503)
        assert (control.store.path.read_bytes() if control.store.path.exists() else None) == before
        assert len(calls) == 1


@pytest.mark.parametrize('phase', ['analysis_requested', 'previewed', 'admitted'])
async def test_configured_audit_failure_stops_each_admission_boundary(tmp_path, monkeypatch, phase):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        audit = control._audit
        monkeypatch.setattr(control, '_audit', lambda action: False if action == phase else audit(action))
        if phase == 'admitted':
            fields = await preview(site, client)
            response = await apply(site, client, fields)
        else:
            response = await upload(site, client)
        assert response.status == 503
        assert len(calls) == (0 if phase == 'analysis_requested' else 1)
        assert not control.store.path.exists()


@pytest.mark.parametrize('fault', ['origin', 'host', 'duplicate_image', 'unknown_field', 'not_jpeg'])
async def test_upload_boundary_refuses_without_analysis_or_retained_grant(tmp_path, monkeypatch, fault):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        headers, extra, image = {}, None, IMAGE
        if fault == 'origin':
            headers['Origin'] = 'https://fictional-other.invalid'
        elif fault == 'host':
            headers['Host'] = 'fictional-other.invalid'
        elif fault == 'duplicate_image':
            extra = [('image', IMAGE)]
        elif fault == 'unknown_field':
            extra = [('arbitrary_path', '/fictional-private-canary')]
        else:
            image = b'GIF89a-FICTIONAL'
        response = await upload(site, client, headers=headers, extra=extra, image=image)
        assert response.status in (403, 409)
        assert calls == [] and not control.grants and not control.store.path.exists()


async def test_expiry_and_service_cleanup_discard_transient_vectors(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, _):
        fields = await preview(site, client)
        grant = control.grants[fields['grant']]
        vector = grant['template']
        # Invoke the real registered timer callback without a two-minute sleep.
        grant['timer']._callback(*grant['timer']._args)
        assert not control.grants and vector == []
        fields = await preview(site, client)
        vector = control.grants[fields['grant']]['template']
        await control.cleanup(None)
        assert not control.grants and vector == [] and control.closed
        assert (await apply(site, client, fields)).status == 409


@pytest.mark.parametrize('change', ['configuration', 'store'])
async def test_last_moment_changes_are_checked_inside_serialized_commit(tmp_path, monkeypatch, change):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, key, calls):
        fields = await preview(site, client)
        original = control._audit
        def audit(action):
            if action == 'admitted':
                if change == 'configuration':
                    key.write_bytes(key.read_bytes() + b'\n')
                else:
                    control.store.enroll('Fictional concurrent identity', VECTOR, embedding_space=SPACE)
            return original(action)
        monkeypatch.setattr(control, '_audit', audit)
        response = await apply(site, client, fields)
        assert response.status == 409
        assert NAME not in control.store.names() and not control.grants
        assert len(calls) == 1


@pytest.mark.parametrize('outcome', ['uncertain_after_write', 'saved_audit_failure'])
async def test_possible_committed_enrollment_is_never_automatically_retried(tmp_path, monkeypatch, outcome):
    from aikey.faces import FaceStoreOutcomeUncertain
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        first = await preview(site, client)
        sibling = await preview(site, client)
        if outcome == 'uncertain_after_write':
            original = control.store.enroll
            def uncertain(*args, **kwargs):
                original(*args, **kwargs)
                raise FaceStoreOutcomeUncertain('face_store_write_uncertain')
            monkeypatch.setattr(control.store, 'enroll', uncertain)
        else:
            audit = control._audit
            monkeypatch.setattr(control, '_audit', lambda a: False if a == 'saved' else audit(a))
        response = await apply(site, client, first)
        assert response.status == 503 and 'outcome uncertain' in (await response.text()).lower()
        assert control.store.snapshot()['identities'][0]['templates'] == 1
        assert not control.grants
        assert (await apply(site, client, first)).status == 409
        assert (await apply(site, client, sibling)).status == 409
        assert len(calls) == 2


async def test_local_server_model_mismatch_never_offers_save_or_changes_old_record(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        control.store.enroll('Fictional old identity', VECTOR, embedding_space='b' * 64)
        before = control.store.path.read_bytes()
        response = await upload(site, client)
        assert response.status == 409 and '<form' not in await response.text()
        assert len(calls) == 1 and not control.grants and control.store.path.read_bytes() == before


async def test_revoked_session_after_analysis_never_mints_confirmation(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, cookie, control, _, calls):
        original = face_enrollment_control.embed_single_face
        def revoke(*args):
            value = original(*args)
            site.security.revoke(cookie)
            return value
        monkeypatch.setattr(face_enrollment_control, 'embed_single_face', revoke)
        response = await upload(site, client)
        assert response.status == 403
        assert len(calls) == 1 and not control.grants and not control.store.path.exists()


async def test_cancelled_http_waiter_keeps_one_real_analyzer_owned_until_thread_ends(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from aikey.model_work_fence import ModelWorkFence, initialize
    async with fixture(tmp_path, monkeypatch) as (site, _, cookie, control, key, calls):
        initialize(key.parent, '0' * 32)
        fence = ModelWorkFence.from_state(key.parent)
        status = await control._status()
        entered, release = threading.Event(), threading.Event()
        def blocked(image, endpoint):
            calls.append((image, endpoint))
            entered.set()
            assert release.wait(5)
            return list(VECTOR), SPACE
        monkeypatch.setattr(face_enrollment_control, 'embed_single_face', blocked)
        fields = {'csrf': site.security.csrf_token(cookie), 'revision': status['store']['revision'],
            'config_revision': status['config_revision'], 'consent': 'yes', 'name': NAME}
        async def parsed(_):
            return fields, IMAGE
        monkeypatch.setattr(control, '_upload', parsed)
        monkeypatch.setattr(site, '_session', lambda *_a, **_k: cookie)
        request = SimpleNamespace(headers={'Origin': site.origin})
        waiter = asyncio.create_task(control.preview(request))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert control.busy and (await control.preview(request)).status == 503
            assert fence.try_exclusive() is None
            assert len(calls) == 1 and not control.store.path.exists()
        finally:
            release.set()
            await site.admin_api.drain(None)
        assert not control.busy and not control.grants and not control.store.path.exists()
        exclusive = fence.try_exclusive()
        assert exclusive is not None
        exclusive.close()
        fence.close()


async def test_logout_immediately_clears_only_own_pending_templates(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, cookie, control, _, _):
        fields = await preview(site, client)
        vector = control.grants[fields['grant']]['template']
        response = await client.post('/logout', data={'csrf': site.security.csrf_token(cookie)},
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303
        assert vector == [] and not control.grants and not control.store.path.exists()


async def test_deletion_and_enrollment_invalidate_each_others_old_confirmations(tmp_path, monkeypatch):
    from test_face_identity_control import preview as deletion_preview
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, _):
        control.store.enroll('Fictional original identity', VECTOR, embedding_space=SPACE)
        enrollment = await preview(site, client)
        deletion = await deletion_preview(site, client)
        response = await client.post('/face-identities/apply', data=deletion,
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303 and not control.grants
        assert (await apply(site, client, enrollment)).status == 409
        control.store.enroll('Fictional original identity', VECTOR, embedding_space=SPACE)
        deletion = await deletion_preview(site, client)
        enrollment = await preview(site, client)
        assert (await apply(site, client, enrollment)).status == 303
        assert not site.admin_api.face_identity_control.grants
        response = await client.post('/face-identities/apply', data=deletion,
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 409


async def test_existing_model_work_fence_excludes_analyzer_until_writer_releases(tmp_path, monkeypatch):
    import asyncio
    from aikey.model_work_fence import ModelWorkFence, initialize
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, key, calls):
        initialize(key.parent, '0' * 32)
        fence = ModelWorkFence.from_state(key.parent)
        writer = fence.try_exclusive()
        assert writer is not None
        original = face_enrollment_control.embed_single_face
        def held(*args):
            assert fence.try_exclusive() is None
            return original(*args)
        monkeypatch.setattr(face_enrollment_control, 'embed_single_face', held)
        task = asyncio.create_task(upload(site, client))
        try:
            await asyncio.sleep(.1)
            assert calls == [] and control.busy
            writer.close()
            response = await asyncio.wait_for(task, 3)
            assert response.status == 200 and len(calls) == 1
            exclusive = fence.try_exclusive()
            assert exclusive is not None
            exclusive.close()
        finally:
            writer.close()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            fence.close()


async def test_unsafe_existing_fence_refuses_without_model_call_or_repair(tmp_path, monkeypatch):
    from aikey.model_work_fence import FILENAME
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, key, calls):
        marker = key.parent / FILENAME
        marker.write_bytes(b'FICTIONAL-BROKEN-MARKER')
        marker.chmod(0o600)
        before = marker.read_bytes()
        response = await upload(site, client)
        assert response.status == 503
        assert calls == [] and not control.grants and not control.store.path.exists()
        assert marker.read_bytes() == before


async def test_existing_label_gets_one_template_and_other_identities_are_preserved(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, _):
        control.store.enroll(NAME, VECTOR, embedding_space=SPACE)
        control.store.enroll('Fictional sibling', VECTOR, embedding_space=SPACE)
        before = json.loads(control.store.path.read_bytes())['identities']['Fictional sibling']
        assert (await apply(site, client, await preview(site, client))).status == 303
        raw = json.loads(control.store.path.read_bytes())
        assert len(raw['identities'][NAME]['templates']) == 2
        assert raw['identities']['Fictional sibling'] == before


async def test_pending_template_budget_refuses_before_another_model_request(tmp_path, monkeypatch):
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        for _ in range(8):
            await preview(site, client)
        response = await upload(site, client)
        assert response.status == 503 and len(calls) == 8 and len(control.grants) == 8
        assert not control.store.path.exists()


async def test_cross_session_confirmation_cannot_save_or_cancel_another_session_template(tmp_path, monkeypatch):
    import aiohttp
    from yarl import URL
    from test_control_site_secrets import PASSWORD
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, _):
        fields = await preview(site, client)
        original_grant = control.grants[fields['grant']]
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as other:
            response = await other.post(site.origin + '/login', data={'password': PASSWORD},
                headers={'Origin': site.origin}, allow_redirects=False)
            assert response.status == 303
            cookie = other.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
            foreign = {**fields, 'csrf': site.security.csrf_token(cookie)}
            response = await other.post(site.origin + '/face-identities/enroll/apply', data=foreign,
                headers={'Origin': site.origin}, allow_redirects=False)
            assert response.status == 409
            foreign.pop('confirm')
            response = await other.post(site.origin + '/face-identities/enroll/cancel', data=foreign,
                headers={'Origin': site.origin}, allow_redirects=False)
            assert response.status == 409
        assert control.grants[fields['grant']] is original_grant
        assert not control.store.path.exists()
        assert (await apply(site, client, fields)).status == 303


async def test_cancelled_save_waiter_does_not_cancel_or_repeat_admitted_store_thread(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    async with fixture(tmp_path, monkeypatch) as (site, client, cookie, control, key, calls):
        fields = await preview(site, client)
        entered, release = threading.Event(), threading.Event()
        original = site.aikey.enroll_local_face
        def blocked(*args):
            entered.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(site.aikey, 'enroll_local_face', blocked)
        async def parsed(*_):
            return fields
        monkeypatch.setattr(control.identity, '_fields', parsed)
        monkeypatch.setattr(site, '_session', lambda *_a, **_k: cookie)
        waiter = asyncio.create_task(control.apply(SimpleNamespace()))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not control.grants and site.admin_api.operations
        finally:
            release.set()
            await site.admin_api.drain(None)
        assert control.store.snapshot()['identities'][0]['templates'] == 1
        assert len(calls) == 1


async def test_configuration_writer_lock_stays_held_through_face_store_commit(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    async with fixture(tmp_path, monkeypatch) as (site, _, _, control, key, _):
        entered, release, other_entered = threading.Event(), threading.Event(), threading.Event()
        config_revision = site.aikey.snapshot().revision
        store_revision = control.store.snapshot()['revision']
        original = control.store.enroll
        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(4)
            return original(*args, **kwargs)
        def competing_writer():
            with site.aikey._locked():
                other_entered.set()
        monkeypatch.setattr(control.store, 'enroll', paused)
        before = key.read_bytes()
        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = pool.submit(site.aikey.enroll_local_face, config_revision,
                control.store, NAME, VECTOR, SPACE, store_revision)
            try:
                assert entered.wait(2)
                competing = pool.submit(competing_writer)
                assert not other_entered.wait(.15)
            finally:
                release.set()
            assert admitted.result(timeout=4) == 1
            competing.result(timeout=4)
        assert key.read_bytes() == before and other_entered.is_set()


async def test_stalled_upload_releases_admission_without_any_model_or_store_action(tmp_path, monkeypatch):
    import asyncio
    async with fixture(tmp_path, monkeypatch) as (site, client, _, control, _, calls):
        async def stalled(_):
            await asyncio.sleep(2)
        monkeypatch.setattr(control, '_upload', stalled)
        monkeypatch.setattr(face_enrollment_control, '_UPLOAD_TIMEOUT', .02)
        response = await upload(site, client)
        assert response.status == 503 and calls == []
        assert not control.busy and not control.grants and not control.store.path.exists()


@pytest.mark.parametrize('kind', ['key', 'port'])
async def test_shared_logout_without_admin_api_still_revokes_first_run_sessions(tmp_path, kind):
    from yarl import URL
    from test_first_run_site import site_for as key_site
    from test_first_run_port_site import site_for as port_site
    factory = key_site if kind == 'key' else port_site
    site, server, client, *_ = await factory(tmp_path)
    try:
        assert not hasattr(site, 'admin_api')
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[site.cookie_name].value
        fields = {'csrf': site.security.csrf_token(cookie)}
        response = await client.post('/logout', data=fields,
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303 and response.headers['Location'] == '/login'
        assert not site.security.authorize('GET', site.origin, cookie).allowed
        assert (await client.get('/', allow_redirects=False)).status == 303
        assert not site.directory.exists()
    finally:
        await client.close()
        await server.close()

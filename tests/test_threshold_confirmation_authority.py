"""Actual HTTP replay/expiry races with fictional saved settings only."""

import asyncio
import threading

import pytest

from test_camera_threshold_http import preview, ready
from test_ocr_provider_http import form


async def post(site, client, fields):
    return await client.post('/camera-thresholds/apply', data=fields,
                             headers={'Origin': site.origin}, allow_redirects=False)


async def undo(site, client):
    body = await (await client.get('/camera-thresholds')).text()
    fields = form(body, '/camera-thresholds/undo')
    fields['confirm'] = 'yes'
    response = await client.post('/camera-thresholds/undo', data=fields,
                                headers={'Origin': site.origin}, allow_redirects=False)
    assert response.status == 303


@pytest.mark.parametrize('sibling', [False, True])
async def test_old_confirmation_cannot_reapply_after_exact_undo(tmp_path, sibling):
    site, server, client, _, key, _ = await ready(tmp_path)
    try:
        store = site.aiports['aiport']
        before, key_before = store.path.read_bytes(), key.read_bytes()
        fields = await preview(site, client)
        other = await preview(site, client) if sibling else fields
        assert (await post(site, client, fields)).status == 303
        await undo(site, client)
        assert store.path.read_bytes() == before
        history = {p.name: p.read_bytes() for p in store.history_dir.iterdir() if p.is_file()}
        assert (await post(site, client, other)).status == 409
        assert store.path.read_bytes() == before and key.read_bytes() == key_before
        assert history == {p.name: p.read_bytes() for p in store.history_dir.iterdir() if p.is_file()}
        # A genuinely new review after Undo remains possible.
        assert (await post(site, client, await preview(site, client))).status == 303
    finally:
        await client.close()
        await server.close()


async def test_expiry_is_rechecked_after_slow_preview_despite_wall_clock(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    control = site.admin_api.camera_threshold_control
    elapsed = [10.0]
    monkeypatch.setattr(control, 'clock', lambda: elapsed[0], raising=False)
    try:
        store = site.aiports['aiport']
        fields = await preview(site, client)
        before = store.path.read_bytes()
        original = store.preview_camera_thresholds
        def slow(*args):
            result = original(*args)
            elapsed[0] += 601
            return result
        monkeypatch.setattr(store, 'preview_camera_thresholds', slow)
        # Wall time remains in range; the confirmation has spent >600s in validation.
        assert (await post(site, client, fields)).status == 409
        assert store.path.read_bytes() == before and not store.history_dir.exists()
    finally:
        await client.close()
        await server.close()


async def test_overlapping_same_confirmation_admits_one_store_write(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    barrier = threading.Barrier(2)
    try:
        store = site.aiports['aiport']
        fields = await preview(site, client)
        original_preview, original_apply = store.preview_camera_thresholds, store.apply_camera_thresholds
        calls = []
        def together(*args):
            result = original_preview(*args)
            barrier.wait(timeout=5)
            return result
        def counted(*args):
            calls.append(True)
            return original_apply(*args)
        monkeypatch.setattr(store, 'preview_camera_thresholds', together)
        monkeypatch.setattr(store, 'apply_camera_thresholds', counted)
        responses = await asyncio.gather(post(site, client, fields), post(site, client, fields))
        assert sorted(r.status for r in responses) == [303, 409]
        assert len(calls) == 1
    finally:
        barrier.abort()
        await client.close()
        await server.close()


@pytest.mark.parametrize('outcome', ['no_op', 'uncertain'])
async def test_used_authority_is_not_reusable_without_a_revision_change(tmp_path, monkeypatch, outcome):
    site, server, client, _, _, _ = await ready(tmp_path)
    try:
        store = site.aiports['aiport']
        if outcome == 'no_op':
            assert (await post(site, client, await preview(site, client))).status == 303
        fields = await preview(site, client)
        original, calls = store.apply_camera_thresholds, []
        def apply(*args):
            calls.append(True)
            if outcome == 'uncertain':
                raise OSError('fictional pre-write uncertainty')
            return original(*args)
        monkeypatch.setattr(store, 'apply_camera_thresholds', apply)
        before = store.path.read_bytes()
        assert (await post(site, client, fields)).status == (503 if outcome == 'uncertain' else 303)
        assert store.path.read_bytes() == before
        assert (await post(site, client, fields)).status == 409
        assert len(calls) == 1
    finally:
        await client.close()
        await server.close()


async def test_grants_are_unique_bounded_and_expire_without_evicting_live_reviews(tmp_path, monkeypatch):
    site, server, client, _, _, _ = await ready(tmp_path)
    control = site.admin_api.camera_threshold_control
    elapsed = [1.0]
    monkeypatch.setattr(control, 'clock', lambda: elapsed[0])
    try:
        store = site.aiports['aiport']
        before = store.path.read_bytes()
        reviews = [await preview(site, client) for _ in range(32)]
        assert len({x['signature'] for x in reviews}) == 32
        body = await (await client.get('/camera-thresholds')).text()
        fields = form(body, '/camera-thresholds/preview')
        response = await client.post('/camera-thresholds/preview', data=fields,
                                     headers={'Origin': site.origin})
        assert response.status == 409 and len(control.grants) == 32
        assert store.path.read_bytes() == before and not store.history_dir.exists()
        elapsed[0] += 600
        assert (await post(site, client, reviews[0])).status == 409
        fresh = await preview(site, client)
        assert len(control.grants) == 1
        assert (await post(site, client, fresh)).status == 303
    finally:
        await client.close()
        await server.close()


async def test_revocation_during_validation_and_restart_refuse_old_authority(tmp_path, monkeypatch):
    from aikey.camera_threshold_control import CameraThresholdControl
    site, server, client, cookie, _, _ = await ready(tmp_path)
    try:
        store = site.aiports['aiport']
        control = site.admin_api.camera_threshold_control
        fields = await preview(site, client)
        before = store.path.read_bytes()
        original = store.preview_camera_thresholds
        def revoke(*args):
            result = original(*args)
            site.security.revoke(cookie)
            return result
        monkeypatch.setattr(store, 'preview_camera_thresholds', revoke)
        assert (await post(site, client, fields)).status == 403
        # A new authority cannot validate an old form even if the application
        # session signing key survives. No grants or form key are persisted.
        replacement = CameraThresholdControl(site)
        assert not replacement.grants and replacement.signing_key != control.signing_key
        assert replacement._signature(cookie, fields) != fields['signature']
        assert store.path.read_bytes() == before and not store.history_dir.exists()
    finally:
        await client.close()
        await server.close()

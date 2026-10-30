"""HTML uses shared admission, never host commands or an implicit worker."""

import asyncio
import hashlib
from html.parser import HTMLParser
import json
import os
import time

from multidict import MultiDict
import pytest

from aikey import admin_maintenance as module
from aikey.admin_audit import AuditLog
from test_control_site_secrets import signed_in


CANARY = "fictional-host-value-must-not-reach-page"
CODE = '''import json,pathlib,sys
v=json.load(sys.stdin)
with pathlib.Path(v["effect"]).open("a") as f:f.write("one-effect\\n")
print(json.dumps({"schema":"aikey-maintenance-result/1","outcome":"succeeded"}))
'''


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("action") == "/maintenance":
            self.current = {}
            self.rows.append(self.current)
        if tag == "input" and self.current is not None and "name" in values:
            self.current[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def forms(page):
    parser = Forms()
    parser.feed(page)
    return parser.rows


def offer(tmp_path, site, source, name="first", *, described=True):
    script = tmp_path / (name + ".py")
    script.write_text(CODE)
    host, bridge = tmp_path / "host", tmp_path / "bridge"
    revision = hashlib.sha256(source.read_bytes()).hexdigest()
    operation = module.prepare(host, bridge, script=script,
        inputs={"effect": str(tmp_path / (name + ".effect")), "canary": CANARY},
        kind="backup", profile="aikey", revision=revision, timeout=5,
        expected_effect="private_backup_export", guard_files=[str(source)],
        purpose="device_file_backup" if described else None)
    site.maintenance = module.Bridge(bridge)
    site.audit = AuditLog(tmp_path / "admin-audit.jsonl")
    return host, bridge, operation, revision


async def test_form_double_submit_is_one_queued_request_and_get_never_executes(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        host, bridge, operation, revision = offer(tmp_path, site, source)
        before = source.read_bytes()
        response = await client.get('/maintenance')
        page = await response.text()
        assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
        assert "default-src 'none'" in response.headers['Content-Security-Policy']
        assert 'cold window' in page and 'does not start a host worker' in page
        assert CANARY not in page and str(tmp_path) not in page and cookie not in page
        assert '<script' not in page and 'localStorage' not in page
        one, = forms(page)
        assert one['operation'] == operation and one['revision'] == revision
        assert forms(await (await client.get('/maintenance')).text()) == [one]
        assert list((bridge / 'requests').iterdir()) == []
        responses = await asyncio.gather(*(client.post('/maintenance', data=one,
            headers={'Origin': site.origin}, allow_redirects=False) for _ in range(2)))
        assert [r.status for r in responses] == [303, 303]
        assert all(r.headers['Location'] == '/maintenance/requests/' + one['request'] for r in responses)
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
        assert not (tmp_path / 'first.effect').exists()
        pending = await (await client.get(responses[0].headers['Location'])).text()
        assert 'No host result reported' in pending and 'Host reported success' not in pending
        assert forms(await (await client.get('/maintenance')).text()) == []
        assert module.work_once(host, bridge) == {'handled': 1}
        assert (tmp_path / 'first.effect').read_text() == 'one-effect\n'
        page = await (await client.get('/maintenance')).text()
        assert 'Already submitted' in page and 'Host reported success' in page and forms(page) == []
        assert (await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 303
        assert module.work_once(host, bridge) == {'handled': 0}
        assert source.read_bytes() == before
        response = await client.get('/api/admin/v1/maintenance/requests')
        history = await response.json()
        assert history['requests'][0]['state'] == 'succeeded'
        assert CANARY not in json.dumps(history) and str(tmp_path) not in json.dumps(history)
        rows = site.audit.recent()
        assert any(r['action'] == 'maintenance_submit' and r['result'] == 'admitted'
                   and r['operation_id'] == operation and r['request_id'] == one['request'] for r in rows)
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize('bad', ['csrf', 'origin', 'request', 'revision', 'extra', 'duplicate', 'confirm', 'json'])
async def test_bad_forms_never_admit_host_work(tmp_path, bad):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        origin = site.origin
        if bad == 'csrf':
            one['csrf'] = 'invalid'
        elif bad == 'origin':
            origin = 'https://unowned.invalid'
        elif bad == 'request':
            one['request'] = 'f' * 32
        elif bad == 'revision':
            one['revision'] = 'f' * 64
        elif bad == 'extra':
            one['command'] = 'fictional-no-execution'
        elif bad == 'duplicate':
            one = MultiDict([*one.items(), ('operation', one['operation'])])
        elif bad == 'confirm':
            del one['confirm']
        response = await client.post('/maintenance', headers={'Origin': origin},
            **({'json': one} if bad == 'json' else {'data': one}), allow_redirects=False)
        assert response.status in {400, 403, 409}
        assert not list((bridge / 'requests').glob('*.json'))
        assert not (tmp_path / 'first.effect').exists()
        assert CANARY not in await response.text()
    finally:
        await client.close()
        await server.close()


async def test_unknown_result_blocks_other_plans_without_inventing_worker_liveness(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        host, bridge, _, _ = offer(tmp_path, site, source)
        offer(tmp_path, site, source, 'second')
        both = forms(await (await client.get('/maintenance')).text())
        assert len(both) == 2
        assert (await client.post('/maintenance', data=both[0], headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 303
        page = await (await client.get('/maintenance')).text()
        assert forms(page) == [] and 'no settled outcome' in page
        assert (await client.post('/maintenance', data=both[1], headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 409
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
        assert not list((host / 'ledger').iterdir())
    finally:
        await client.close()
        await server.close()


async def test_absent_legacy_withdrawn_and_expired_views_never_offer_false_actions(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        page = await (await client.get('/maintenance')).text()
        assert 'No host maintenance connection' in page and forms(page) == []
        _, bridge, operation, _ = offer(tmp_path, site, source, described=False)
        page = await (await client.get('/maintenance')).text()
        assert 'no reviewable built-in scope' in page and forms(page) == []
        (bridge / 'catalog' / (operation + '.json')).unlink()
        page = await (await client.get('/maintenance')).text()
        assert 'No plans have been approved' in page and forms(page) == []
        site.security._clock = lambda: time.time() + 86400
        assert (await client.get('/maintenance', allow_redirects=False)).status == 303
        assert (await client.get('/api/admin/v1/maintenance/requests')).status == 401
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize('damage', ['fifo', 'receipt'])
async def test_unsafe_or_contradictory_history_is_not_a_success_or_partial_page(tmp_path, damage):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        host, bridge, operation, revision = offer(tmp_path, site, source)
        if damage == 'fifo':
            os.mkfifo(bridge / 'requests' / ('a' * 32 + '.json'), 0o600)
        else:
            site.maintenance.submit(operation, revision, 'b' * 32)
            module.work_once(host, bridge)
            p = bridge / 'receipts' / ('b' * 32 + '.json')
            row = json.loads(p.read_text())
            row['operation'] = 'f' * 64
            p.write_text(json.dumps(row))
        response = await asyncio.wait_for(client.get('/maintenance'), timeout=2)
        assert response.status == 503
        page = await response.text()
        assert forms(page) == [] and 'Host reported success' not in page and CANARY not in page
        assert (await client.get('/api/admin/v1/maintenance/requests')).status == 503
    finally:
        await client.close()
        await server.close()


async def test_settings_change_after_review_disables_and_refuses_the_old_form(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        config = json.loads(source.read_text())
        config['worker']['max_queue'] = 17
        source.write_text(json.dumps(config) + '\n')
        changed = source.read_bytes()
        page = await (await client.get('/maintenance')).text()
        assert 'Saved settings no longer match' in page and forms(page) == []
        assert (await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 409
        assert not list((bridge / 'requests').glob('*.json')) and source.read_bytes() == changed
    finally:
        await client.close()
        await server.close()


async def test_html_admission_shares_the_existing_runtime_mutation_window(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        for _ in range(59):
            response = await client.post('/role-model', data={'csrf': one['csrf'],
                'profile': 'unconfigured-proof', 'role': 'caption', 'revision': '0' * 64,
                'model': 'fictional-no-save'}, headers={'Origin': site.origin})
            assert response.status == 400
        assert (await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 303
        limited = await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                    allow_redirects=False)
        assert limited.status == 429 and 1 <= int(limited.headers['Retry-After']) <= 60
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
        assert (await client.get('/maintenance')).status == 200
        assert (await client.post('/logout', data={'csrf': one['csrf']},
                                  headers={'Origin': site.origin}, allow_redirects=False)).status == 303
    finally:
        await client.close()
        await server.close()


async def test_uncertain_publication_links_same_request_without_claiming_no_effect(tmp_path, monkeypatch):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        original = site.maintenance.submit
        def lost_ack(*args):
            original(*args)
            raise OSError('fictional-private-IO-detail')
        monkeypatch.setattr(site.maintenance, 'submit', lost_ack)
        response = await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                     allow_redirects=False)
        page = await response.text()
        assert response.status == 503 and 'uncertain outcome' in page
        assert '/maintenance/requests/' + one['request'] in page
        assert 'fictional-private-IO-detail' not in page and 'No request was submitted' not in page
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
        assert (await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                  allow_redirects=False)).status == 303
        assert 'No host result reported' in await (await client.get('/maintenance/requests/' + one['request'])).text()
    finally:
        await client.close()
        await server.close()


async def test_session_expiring_during_metadata_read_cannot_admit_a_job(tmp_path, monkeypatch):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        original = site.maintenance.history
        def expire():
            result = original()
            site.security._clock = lambda: time.time() + 86400
            return result
        monkeypatch.setattr(site.maintenance, 'history', expire)
        response = await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                     allow_redirects=False)
        assert response.status == 403 and not list((bridge / 'requests').glob('*.json'))
    finally:
        await client.close()
        await server.close()


async def test_past_result_is_readable_when_current_config_and_catalog_are_damaged(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        host, bridge, operation, revision = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        response = await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                     allow_redirects=False)
        module.work_once(host, bridge)
        source.write_text('fictional-broken-config')
        (bridge / 'catalog' / (operation + '.json')).write_text('fictional-broken-catalog')
        assert (await client.get('/maintenance')).status == 503
        result = await client.get(response.headers['Location'])
        assert result.status == 200 and 'Host reported success' in await result.text()
        assert (tmp_path / 'first.effect').read_text() == 'one-effect\n'
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
    finally:
        await client.close()
        await server.close()


async def test_update_review_shows_only_digest_and_fixed_scope(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        script = tmp_path / 'approved.py'
        script.write_text(CODE)
        host, bridge = tmp_path / 'host', tmp_path / 'bridge'
        revision = hashlib.sha256(source.read_bytes()).hexdigest()
        operation = module.prepare(host, bridge, script=script,
            inputs={'image': 'private-registry.invalid/private/image@sha256:' + 'a' * 64},
            kind='update', profile='aikey', revision=revision, timeout=5,
            expected_effect='offline_replace', guard_files=[str(source)], purpose='apple_image_update')
        site.maintenance = module.Bridge(bridge)
        site.audit = AuditLog(tmp_path / 'admin-audit.jsonl')
        page = await (await client.get('/maintenance')).text()
        assert 'Update the Apple-hosted device' in page and 'sha256:' + 'a' * 64 in page
        assert 'private-registry.invalid' not in page and str(tmp_path) not in page
        assert len(forms(page)) == 1 and not list((bridge / 'requests').iterdir())
        path = bridge / 'catalog' / (operation + '.json')
        row = json.loads(path.read_text())
        row.pop('target_image')
        path.write_text(json.dumps(row))
        page = await (await client.get('/maintenance')).text()
        assert forms(page) == [] and 'Not available here' in page
    finally:
        await client.close()
        await server.close()


async def test_different_concurrent_forms_do_not_both_pass_empty_history(tmp_path, monkeypatch):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        offer(tmp_path, site, source, name='second')
        rows = forms(await (await client.get('/maintenance')).text())
        assert len(rows) == 2
        original = site.maintenance.submit
        def delayed(*args):
            time.sleep(0.1)  # Hold only this fictional file publication, not a real host worker.
            return original(*args)
        monkeypatch.setattr(site.maintenance, 'submit', delayed)
        results = await asyncio.gather(*(client.post('/maintenance', data=row,
            headers={'Origin': site.origin}, allow_redirects=False) for row in rows))
        assert sorted(r.status for r in results) == [303, 409]
        assert len(list((bridge / 'requests').glob('*.json'))) == 1
        assert not list(tmp_path.glob('*.effect'))
    finally:
        await client.close()
        await server.close()


async def test_bridge_without_admission_audit_cannot_offer_or_queue_forms(tmp_path):
    site, server, client, cookie, source = await signed_in(tmp_path)
    try:
        _, bridge, _, _ = offer(tmp_path, site, source)
        one, = forms(await (await client.get('/maintenance')).text())
        site.audit = None
        page = await (await client.get('/maintenance')).text()
        assert forms(page) == [] and 'Admission auditing is unavailable' in page
        response = await client.post('/maintenance', data=one, headers={'Origin': site.origin},
                                     allow_redirects=False)
        assert response.status == 409 and not list((bridge / 'requests').glob('*.json'))
    finally:
        await client.close()
        await server.close()

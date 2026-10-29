"""One-use cost-acknowledged probes are authenticated, role-bound and non-mutating."""

import asyncio
from html.parser import HTMLParser
import json

from aiohttp import web
import pytest
from yarl import URL

from aikey import provider_probe as probe
from aikey.control_site import _COOKIE
from test_control_site_catalog import configured
from test_control_site_secrets import PASSWORD, assert_private_page
from test_provider_probe import ANSWER, fixture_image, reply_for


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.forms = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {} if attrs.get('action') == '/provider-probe' else None
        if tag == 'input' and self.current is not None and attrs.get('type') == 'hidden':
            self.current[attrs['name']] = attrs.get('value', '')
    def handle_endtag(self, tag):
        if tag == 'form':
            if self.current is not None:
                self.forms.append(self.current)
            self.current = None


async def form(client, *, profile='aikey', role='caption'):
    response = await client.get('/')
    body = await response.text()
    forms = Forms()
    forms.feed(body)
    selected = next(f for f in forms.forms if f['profile'] == profile and f['role'] == role)
    return {**selected, 'acknowledge_cost': 'yes'}, body


@pytest.mark.parametrize('profile,role,expected', [('aikey', 'caption', 'vision'),
                                               ('aiport', 'detection', 'detection')])
async def test_real_form_sends_only_its_saved_role_and_cannot_replay(
        tmp_path, monkeypatch, profile, role, expected):
    seen = []
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        seen.append((request.path, request.headers.get('Authorization'), await request.json()))
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, page = await form(client, profile=profile, role=role)
        assert f'/{expected}/v1' in page and 'Provider charges may apply' in page
        assert "name='acknowledge_cost' value='yes' required" in page
        assert "name='acknowledge_cost' value='yes' required checked" not in page
        before = (path.read_bytes(), site.aiport.path.read_bytes())
        result = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        body = await result.text()
        assert result.status == 200 and 'generated image answer matched' in body
        assert_private_page(result, body)
        assert 'fictional-camera' not in body and 'api_key_file' not in body
        assert f'synthetic-{expected}-credential' not in body and ANSWER not in body
        assert f'role: {role}' in body and 'not general modality' in body
        assert [(p, h) for p, h, _ in seen] == [
            (f'/{expected}/v1/chat/completions', f'Bearer synthetic-{expected}-credential')]
        assert before == (path.read_bytes(), site.aiport.path.read_bytes())
        site._probe_grants.next_at = 0
        replay = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        assert replay.status == 409 and len(seen) == 1
    finally:
        await client.close()
        await server.close()
        await upstream.close()


@pytest.mark.parametrize('failure', [
    'csrf', 'origin', 'revision', 'grant', 'role', 'profile', 'acknowledge_cost',
    'extra', 'duplicate', 'expired_session', 'expired_grant', 'restart', 'other_session',
])
async def test_invalid_forms_never_contact_provider(tmp_path, failure):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        origin = site.origin
        if failure == 'origin':
            origin = 'http://untrusted.invalid'
        elif failure == 'extra':
            fields['base_url'] = str(upstream.make_url('/unapproved'))
        elif failure == 'duplicate':
            fields = list(fields.items()) + [('profile', 'aiport')]
        elif failure == 'expired_session':
            now = site.security._clock()
            site.security._clock = lambda: now + site.security.session_ttl + 1
        elif failure == 'expired_grant':
            site._probe_grants.clock = lambda: 1e20
        elif failure == 'restart':
            site._probe_grants = probe.ProbeGrants()
        elif failure == 'other_session':
            fields['csrf'] = 'another-session-token'
        else:
            fields[failure] = 'invalid'
        before = (path.read_bytes(), site.aiport.path.read_bytes())
        response = await client.post('/provider-probe', data=fields, headers={'Origin': origin})
        assert response.status in {400, 403, 409} and seen == []
        assert before == (path.read_bytes(), site.aiport.path.read_bytes())
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_busy_probe_is_not_duplicated_and_stale_result_is_not_current(tmp_path, monkeypatch):
    entered, release, seen = asyncio.Event(), asyncio.Event(), []
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        entered.set()
        await release.wait()
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        other, _ = await form(client)
        original = path.read_bytes()
        task = asyncio.create_task(client.post('/provider-probe', data=fields,
                                              headers={'Origin': site.origin}))
        await entered.wait()
        refused = await client.post('/provider-probe', data=other, headers={'Origin': site.origin})
        assert refused.status == 429 and len(seen) == 1
        changed = json.loads(original)
        changed['inference']['model'] = 'new-synthetic-model'
        path.write_text(json.dumps(changed) + '\n')
        expected = path.read_bytes()
        release.set()
        response = await task
        body = await response.text()
        assert 'Synthetic result is stale' in body and 'answer matched' not in body
        assert path.read_bytes() == expected and not site._probe_grants.busy
        assert len(seen) == 1
    finally:
        release.set()
        await client.close()
        await server.close()
        await upstream.close()


async def test_uncertain_failure_consumes_grant_and_never_reflects_keys_or_retries(tmp_path, monkeypatch):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        return web.json_response({'error': 'synthetic-vision-credential'}, status=503)
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        before = path.read_bytes()
        response = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        body = await response.text()
        assert 'may have been charged' in body and 'synthetic-vision-credential' not in body
        assert 'no automatic retry' in body and path.read_bytes() == before
        site._probe_grants.next_at = 0
        assert (await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})).status == 409
        assert len(seen) == 1 and not site._probe_grants.busy
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_post_probe_config_read_failure_is_stale_not_an_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        await request.read()
        path.write_text('invalid synthetic config')
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        response = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        assert response.status == 200 and 'Synthetic result is stale' in await response.text()
        assert path.read_text() == 'invalid synthetic config' and not site._probe_grants.busy
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_valid_second_session_cannot_use_another_sessions_grant(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        await client.post('/login', data={'password': PASSWORD}, headers={'Origin': site.origin})
        other_cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        assert other_cookie != cookie
        other_fields = {**fields, 'csrf': site.security.csrf_token(other_cookie)}
        response = await client.post('/provider-probe', data=other_fields, headers={'Origin': site.origin})
        assert response.status == 409 and seen == []
        client.session.cookie_jar.update_cookies({_COOKIE: cookie}, response_url=URL(site.origin))
        assert (await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})).status == 200
        assert len(seen) == 1
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_unavailable_key_has_no_provider_effect_and_does_not_replay_after_repair(tmp_path):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        key = path.parent / 'vision-key'
        original = key.read_bytes()
        key.unlink()
        before = path.read_bytes()
        response = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        body = await response.text()
        assert 'No provider request was started' in body and 'may have been charged' not in body
        assert seen == [] and path.read_bytes() == before and not site._probe_grants.busy
        key.write_bytes(original)
        key.chmod(0o600)
        site._probe_grants.next_at = 0
        assert (await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})).status == 409
        assert seen == []
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_session_expiring_during_probe_does_not_receive_a_success_result(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        await request.read()
        now = site.security._clock()
        site.security._clock = lambda: now + site.security.session_ttl + 1
        return web.json_response(reply_for('openai-compatible'))
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client)
        response = await client.post('/provider-probe', data=fields, headers={'Origin': site.origin})
        assert response.status == 403 and not site._probe_grants.busy
    finally:
        await client.close()
        await server.close()
        await upstream.close()

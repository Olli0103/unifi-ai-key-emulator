"""Guarded sync dialing: checked sockaddr only, bounded attempts, no real remote peers."""

import asyncio
import http.client
import socket
import ssl

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey import provider_http, aiport_api_detection as api
from aikey.provider_network import ProviderAddressError
from aikey.tls import ensure_identity_certificate, server_context
from test_aiport_api_detection import FIRST, STILL


def peers(*addresses):
    return [(socket.AF_INET6 if ':' in a else socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, '', (a, 443, 0, 0) if ':' in a else (a, 443)) for a in addresses]


class Socket:
    def __init__(self, on_connect=None):
        self.closed, self.peer, self.timeout = False, None, None
        self.on_connect = on_connect
    def settimeout(self, timeout):
        self.timeout = timeout
    def connect(self, peer):
        self.peer = peer
        if self.on_connect:
            self.on_connect()
    def close(self):
        self.closed = True


@pytest.mark.parametrize('url', ['file:///never-open-this', 'data:text/plain,synthetic',
                                'ftp://configured.example/synthetic'])
def test_provider_opener_is_http_only(url):
    with pytest.raises(ProviderAddressError):
        provider_http.provider_opener().open(url, timeout=2)


@pytest.mark.parametrize('factory', [provider_http.ProviderHTTPConnection,
                                   provider_http.ProviderHTTPSConnection])
def test_proxy_tunnels_are_refused_before_connect(monkeypatch, factory):
    calls = []
    monkeypatch.setattr(provider_http, '_connect', lambda *a: calls.append(a))
    connection = factory('localhost', timeout=2)
    connection.set_tunnel('configured.example', 443)
    with pytest.raises(ProviderAddressError):
        connection.connect()
    assert calls == []


@pytest.mark.parametrize('factory,parent', [
    (provider_http.ProviderHTTPConnection, http.client.HTTPConnection),
    (provider_http.ProviderHTTPSConnection, http.client.HTTPSConnection),
])
def test_late_http_or_tls_connect_cannot_return_an_expired_connection(monkeypatch, factory, parent):
    clock = [0]
    s = Socket()
    monkeypatch.setattr(provider_http.time, 'monotonic', lambda: clock[0])
    def late(connection):
        connection.sock = s
        clock[0] = 3
    monkeypatch.setattr(parent, 'connect', late)
    connection = factory('localhost', timeout=2)
    with pytest.raises(TimeoutError):
        connection.connect()
    assert s.closed and connection.sock is None


@pytest.mark.parametrize('answers', [
    peers('127.0.0.1'), peers('8.8.8.8', '10.0.0.1'), peers('::ffff:192.168.1.1'),
    peers('fe80::1'), peers('64:ff9b::7f00:1'), peers('4000::1'), [],
])
def test_sync_dial_checks_all_answers_before_creating_any_socket(monkeypatch, answers):
    calls = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: answers)
    monkeypatch.setattr(socket, 'socket', lambda *a: calls.append(a))
    with pytest.raises(ProviderAddressError):
        provider_http._connect(('configured.example', 443), 2)
    assert calls == []


def test_numeric_transport_peer_is_the_checked_answer_without_second_lookup(monkeypatch):
    lookups, made = [], []
    def lookup(*args, **kwargs):
        lookups.append(args)
        return peers('8.8.8.8') if len(lookups) == 1 else peers('127.0.0.1')
    def make(*args):
        s = Socket()
        made.append(s)
        return s
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    monkeypatch.setattr(socket, 'socket', make)
    assert provider_http._connect(('configured.example', 443), 2) is made[0]
    assert made[0].peer == ('8.8.8.8', 443) and len(lookups) == 1


def test_dial_owns_the_resolver_sequence_after_validation(monkeypatch):
    answers = peers('8.8.8.8')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: answers)
    original = provider_http.checked_addresses
    def checked(*args):
        result = original(*args)
        answers[0] = peers('127.0.0.1')[0]
        return result
    monkeypatch.setattr(provider_http, 'checked_addresses', checked)
    s = Socket()
    monkeypatch.setattr(socket, 'socket', lambda *a: s)
    assert provider_http._connect(('configured.example', 443), 2) is s
    assert s.peer == ('8.8.8.8', 443)


@pytest.mark.parametrize('timeout', [None, 0, -1, float('inf'), float('nan'), True, 10 ** 400])
def test_invalid_connect_budget_fails_before_dns_or_socket(monkeypatch, timeout):
    calls = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: calls.append('dns'))
    monkeypatch.setattr(socket, 'socket', lambda *a: calls.append('socket'))
    with pytest.raises(ProviderAddressError):
        provider_http._connect(('configured.example', 443), timeout)
    assert calls == []


def test_late_dns_completion_does_not_start_a_late_connection(monkeypatch):
    clock, made = [0], []
    monkeypatch.setattr(provider_http.time, 'monotonic', lambda: clock[0])
    def slow(*a, **kw):
        clock[0] = 3
        return peers('8.8.8.8')
    monkeypatch.setattr(socket, 'getaddrinfo', slow)
    monkeypatch.setattr(socket, 'socket', lambda *a: made.append(a))
    with pytest.raises(TimeoutError):
        provider_http._connect(('configured.example', 443), 2)
    assert made == []


@pytest.mark.parametrize('late_success', [False, True])
def test_candidate_attempts_share_budget_and_late_success_is_closed(monkeypatch, late_success):
    clock, made = [0], []
    monkeypatch.setattr(provider_http.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: peers('8.8.8.8', '1.1.1.1'))
    def connect():
        clock[0] = 3
        if not late_success:
            raise OSError('synthetic failure')
    def make(*args):
        s = Socket(connect)
        made.append(s)
        return s
    monkeypatch.setattr(socket, 'socket', make)
    with pytest.raises(TimeoutError):
        provider_http._connect(('configured.example', 443), 2)
    assert len(made) == 1 and made[0].closed and made[0].timeout == 2


def test_interrupt_closes_provisional_socket_without_retry(monkeypatch):
    def interrupt():
        raise KeyboardInterrupt()
    s = Socket(interrupt)
    monkeypatch.setattr(socket, 'socket', lambda *a: s)
    with pytest.raises(KeyboardInterrupt):
        provider_http._connect(('127.0.0.1', 443), 2)
    assert s.closed


def test_dns_outage_keeps_its_fixed_subtype_and_policy_failure_is_not_fallback(monkeypatch):
    def unavailable(*a, **kw):
        raise socket.gaierror('SYNTHETIC-RESOLVER-CANARY')
    monkeypatch.setattr(socket, 'getaddrinfo', unavailable)
    with pytest.raises(api.ApiDetectionError, match='^api_detection_dns_unavailable$') as error:
        api._post('https://configured.example/v1/probe', {}, {})
    assert 'CANARY' not in str(error.value)
    assert api.ApiObjectDetector._fallback_reason(api.ApiDetectionError('api_detection_address_refused')) is None


def test_policy_rejection_before_paid_budget_does_not_select_local_fallback(tmp_path, monkeypatch):
    key = tmp_path / 'fixture-key'
    key.write_text('synthetic-only')
    key.chmod(0o600)
    detector = api.ApiObjectDetector({'provider': 'openai', 'model': 'gpt-6-luna',
        'api_key_file': str(key), 'allow_remote': True}, tmp_path, threshold=0.8,
        max_requests_per_hour=2)
    calls = []
    detector.transport = lambda *a: calls.append('primary')
    detector.fallback = object()
    detector._fallback_text = lambda *a: calls.append('fallback')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: peers('8.8.8.8', '127.0.0.1'))
    with pytest.raises(api.ApiDetectionError, match='^api_detection_address_refused$'):
        detector.detect_for_camera(FIRST, STILL)
    assert calls == [] and detector.budget.remaining(FIRST) == 2


@pytest.mark.parametrize('trusted', [True, False])
async def test_sync_provider_preserves_tls_sni_host_and_role_key(tmp_path, monkeypatch, trusted):
    cert, _ = ensure_identity_certificate(tmp_path, '020000000027')
    tls = server_context(tmp_path)
    names, hits = [], []
    tls.set_servername_callback(lambda sock, name, context: names.append(name))
    async def endpoint(request):
        hits.append((request.host, request.headers.get('Authorization')))
        await request.read()
        return web.json_response({'synthetic': True})
    app = web.Application()
    app.router.add_post('/v1/probe', endpoint)
    server = TestServer(app, scheme='https')
    await server.start_server(ssl=tls)
    ctx = ssl.create_default_context(cafile=str(cert) if trusted else None)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(api, 'ProviderHTTPSHandler', lambda: provider_http.ProviderHTTPSHandler(context=ctx))
    try:
        url = f'https://localhost:{server.port}/v1/probe'
        if trusted:
            assert await asyncio.to_thread(api._post, url, {'Authorization': 'Bearer SYNTHETIC'}, {}) == {'synthetic': True}
            assert hits == [(f'localhost:{server.port}', 'Bearer SYNTHETIC')]
        else:
            with pytest.raises(api.ApiDetectionError):
                await asyncio.to_thread(api._post, url, {}, {})
            assert hits == []
        assert names == ['localhost']
    finally:
        await server.close()

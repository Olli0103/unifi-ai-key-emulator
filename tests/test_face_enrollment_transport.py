"""Enrollment uses synthetic bytes/vectors and disposable loopback servers only."""

import asyncio
import math

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey import faces, provider_http
from aikey.provider_network import ProviderAddressError

VECTOR = [1.0] + [0.0] * 127


async def test_enrollment_does_not_follow_redirect_even_to_another_loopback_server():
    forwarded = []
    async def receiver(request):
        forwarded.append(request.method)
        return web.json_response({"faces": [{"embedding": VECTOR}]})
    receiver_app = web.Application()
    receiver_app.router.add_route('*', '/redirected', receiver)
    async with TestServer(receiver_app) as receiver_server:
        async def initial(request):
            await request.read()
            raise web.HTTPFound(str(receiver_server.make_url('/redirected')))
        app = web.Application()
        app.router.add_post('/v1/faces', initial)
        async with TestServer(app) as server:
            with pytest.raises(faces.FaceStoreError):
                await asyncio.to_thread(faces.embed_single_face, b'SYNTHETIC-PHOTO',
                                        str(server.make_url('')).rstrip('/'))
    assert forwarded == []


async def test_enrollment_does_not_use_an_environment_proxy(monkeypatch):
    import urllib.request
    proxied, direct = [], []
    async def proxy(request):
        proxied.append(await request.read())
        return web.json_response({"faces": [{"embedding": VECTOR}]})
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', proxy)
    async with TestServer(app) as proxy_server:
        monkeypatch.setenv('HTTP_PROXY', str(proxy_server.make_url('')))
        monkeypatch.setenv('http_proxy', str(proxy_server.make_url('')))
        monkeypatch.setenv('NO_PROXY', '')
        monkeypatch.setenv('no_proxy', '')
        monkeypatch.setattr(urllib.request, '_opener', None)
        def no_actual_lan(address, timeout, source_address=None):
            direct.append(address)
            raise ProviderAddressError()
        monkeypatch.setattr(provider_http, '_connect', no_actual_lan)
        with pytest.raises(faces.FaceStoreError):
            await asyncio.to_thread(faces.embed_single_face, b'SYNTHETIC-PHOTO',
                                    'http://10.20.30.40:1234')
        assert direct == [('10.20.30.40', 1234)] and proxied == []


@pytest.mark.parametrize('server', [
    'http://u:p@127.0.0.1', 'http://127.0.0.1/path', 'http://127.0.0.1?q=1',
    'http://127.0.0.1#fragment', 'http://127.0.0.1:0', 'http://169.254.169.254',
    'http://0.0.0.0', 'http://[fe80::1]', 1,
])
def test_enrollment_server_shape_and_address_fail_before_contact(server):
    with pytest.raises(faces.FaceStoreError):
        faces._private_server(server)


@pytest.mark.parametrize('magnitude', [1e308, 5e-324])
def test_stored_identity_normalization_never_silently_zeroes_finite_vectors(tmp_path, magnitude):
    store = faces.FaceStore(tmp_path)
    vector = faces._unit([magnitude] * 128)
    assert math.hypot(*vector) == pytest.approx(1)
    store.enroll('Synthetic Identity', [magnitude] * 128)
    assert store.match([1.0] * 128)[0] == 'Synthetic Identity'


def test_unrepresentable_identity_scalar_has_a_fixed_failure():
    with pytest.raises(faces.FaceStoreError, match='^invalid_embedding$'):
        faces._unit([10 ** 400] * 128)


@pytest.mark.parametrize('reply', [
    {'faces': []}, {'faces': [{'embedding': VECTOR}, {'embedding': VECTOR}]},
    {'faces': [{'embedding': [0.0] * 128}]}, {'faces': [{'embedding': [1.0]}]},
    {'faces': [{'embedding': [float('nan')] * 128}]},
    {'faces': [{'embedding': VECTOR}], 'padding': 'x' * (256 * 1024)},
])
async def test_bad_or_oversized_enrollment_reply_is_refused(reply):
    async def endpoint(request):
        await request.read()
        return web.json_response(reply)
    app = web.Application()
    app.router.add_post('/v1/faces', endpoint)
    async with TestServer(app) as server:
        with pytest.raises(faces.FaceStoreError):
            await asyncio.to_thread(faces.embed_single_face, b'SYNTHETIC-PHOTO',
                                    str(server.make_url('')).rstrip('/'))


async def test_one_valid_enrollment_reply_retains_exact_normalized_identity():
    uploads = []
    async def endpoint(request):
        uploads.append(await request.read())
        return web.json_response({'faces': [{'embedding': VECTOR}]})
    app = web.Application()
    app.router.add_post('/v1/faces', endpoint)
    async with TestServer(app) as server:
        assert await asyncio.to_thread(faces.embed_single_face, b'SYNTHETIC-PHOTO',
                                      str(server.make_url('')).rstrip('/')) == VECTOR
    assert len(uploads) == 1 and b'SYNTHETIC-PHOTO' in uploads[0]


def test_failed_enrollment_keeps_existing_identity_bytes_and_redacts_cli_error(
        tmp_path, monkeypatch, capsys):
    from urllib.error import URLError
    store = faces.FaceStore(tmp_path)
    store.enroll('Existing Synthetic Identity', VECTOR)
    before = store.path.read_bytes()
    image = tmp_path / 'synthetic.jpg'
    image.write_bytes(b'SYNTHETIC-PHOTO')
    class Broken:
        def open(self, *a, **kw):
            raise URLError('SYNTHETIC-PRIVATE-PROVIDER-CANARY')
    monkeypatch.setattr(faces, 'provider_opener', Broken)
    assert faces.main(['enroll', '--state-dir', str(tmp_path), '--name', 'New Synthetic Identity',
                       '--image', str(image)]) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'CANARY' not in out.err and 'enrollment_unavailable' in out.err
    assert store.path.read_bytes() == before

"""Real loopback catalog GETs reject ambiguous wire JSON, not just parsed dicts."""

import json

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aikey.model_catalog import CatalogError, fetch_catalog
from aikey.providers import VisionProvider


AMBIGUOUS = [
    ('openai', b'{"data":[],"data":[{"id":"fictional-model"}]}'),
    ('openai-compatible', b'{"data":[{"id":"fictional-model"}],"has_more":true,"has_more":false}'),
    ('anthropic', b'{"data":[{"id":"fictional-a","id":"fictional-b"}],"has_more":false}'),
    ('anthropic', b'{"data":[{"id":"fictional-model","capabilities":{"image_input":{"supported":false,"supported":true}}}],"has_more":false}'),
    ('ollama', b'{"models":[{"model":"fictional-local","remote_host":"https://fictional.invalid","remote_host":""}]}'),
    ('ollama', b'{"models":[{"model":"fictional-a"}],"models":[{"model":"fictional-b"}]}'),
    ('openai', b'{"data":[{"id":"fictional-model","created":NaN}]}'),
    ('openai-compatible', b'{"data":[{"id":"fictional-model","created":Infinity}]}'),
    ('anthropic', b'{"data":[{"id":"fictional-model","created":-Infinity}],"has_more":false}'),
    ('ollama', b'{"models":[{"model":"fictional-local","size":1e400}]}'),
    ('openai', '{"data":[{"id":"fictional-model"}]}'.encode('utf-16')),
    ('ollama', '{"models":[{"model":"fictional-model"}]}'.encode('utf-32')),
]


@pytest.mark.parametrize('provider,body', AMBIGUOUS)
async def test_ambiguous_or_nonfinite_wire_reply_never_becomes_a_complete_catalog(provider, body):
    calls = []

    async def handler(request):
        calls.append((request.method, request.path, await request.read()))
        return web.Response(body=body, content_type='application/json')

    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    async with TestServer(app) as server:
        selected = VisionProvider({'provider': provider, 'model': 'not-sent',
            'api_key': 'synthetic-catalog-wire-secret',
            'base_url': str(server.make_url('/api' if provider == 'ollama' else '/v1'))}, lab=True)
        with pytest.raises(CatalogError) as failure:
            await fetch_catalog(selected)
        assert str(failure.value) == 'catalog_response_invalid'
        assert 'synthetic-catalog-wire-secret' not in str(failure.value)
        assert calls == [('GET', '/api/tags' if provider == 'ollama' else '/v1/models', b'')]


async def test_ambiguous_second_page_discards_first_page_without_retry_or_third_request():
    queries = []

    async def handler(request):
        queries.append(dict(request.query))
        if len(queries) == 1:
            return web.json_response({'data': [{'id': 'fictional-first'}],
                'has_more': True, 'last_id': 'fictional-first'})
        return web.Response(body=b'{"data":[{"id":"fictional-second"}],"has_more":true,"has_more":false}',
                            content_type='application/json')

    app = web.Application()
    app.router.add_get('/v1/models', handler)
    async with TestServer(app) as server:
        selected = VisionProvider({'provider': 'anthropic', 'model': 'not-sent',
            'base_url': str(server.make_url('/v1')), 'api_key': 'synthetic-pagination-key'}, lab=True)
        with pytest.raises(CatalogError, match='^catalog_response_invalid$'):
            await fetch_catalog(selected)
        assert queries == [{'limit': '100'}, {'limit': '100', 'after_id': 'fictional-first'}]


@pytest.mark.parametrize('provider', ['openai', 'openai-compatible', 'anthropic', 'ollama'])
async def test_unique_utf8_finite_metadata_still_returns_only_safe_model_fields(provider):
    payload = ({'models': [{'model': 'fictional-local', 'size': 1200, 'details': {'value': 0.25}}]}
               if provider == 'ollama' else
               {'data': [{'id': 'fictional-model', 'created': 1200,
                          'metadata': {'value': 0.25, 'label': 'öffentliche Metadaten'}}],
                'has_more': False})

    async def handler(request):
        return web.Response(body=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                            content_type='application/json')

    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handler)
    async with TestServer(app) as server:
        selected = VisionProvider({'provider': provider, 'model': 'not-sent',
            'api_key': 'synthetic-finite-control-key',
            'base_url': str(server.make_url('/api' if provider == 'ollama' else '/v1'))}, lab=True)
        report = await fetch_catalog(selected)
        assert report['complete'] is True and report['capability_tested'] is False
        assert len(report['models']) == 1
        assert set(report['models'][0]) == {'id', 'image_input_advertised',
                                           'provider_reported_remote', 'capability_tested'}
        assert 'Metadaten' not in json.dumps(report) and 'created' not in json.dumps(report)

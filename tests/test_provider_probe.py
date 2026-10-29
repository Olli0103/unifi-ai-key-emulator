"""Synthetic vision probes use disposable loopback providers, never camera data."""

import asyncio
import base64
import gzip
from io import BytesIO
import json
import re

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image
import pytest

from aikey import provider_probe as probe
from aikey.providers import VisionProvider
from aikey.provider_network import ProviderConnector
from test_provider_network import Resolver

ANSWER = "12AB90CD"
KEY = "SYNTHETIC-PROBE-ROLE-KEY"


def fixture_image():
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, "PNG")
    return output.getvalue()


def reply_for(provider, text=ANSWER):
    if provider == "openai":
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "status": "completed", "content": [{"type": "output_text", "text": text}]}]}
    if provider == "anthropic":
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": text}]}
    if provider == "ollama":
        return {"done": True, "done_reason": "stop", "message": {"content": text}}
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def test_generated_challenge_has_only_synthetic_pixels_and_an_unpredictable_label():
    raw, answer = probe._challenge()
    assert re.fullmatch(r"[0-9A-F]{8}", answer)
    with Image.open(BytesIO(raw)) as picture:
        assert picture.format == "PNG" and picture.size == (320, 96)
        assert picture.info == {} and picture.getextrema() == ((0, 255),) * 3
    assert answer not in json.dumps({"prompt": "Read the eight characters in the image."})


@pytest.mark.parametrize("provider,path", [("openai", "/v1/responses"),
    ("anthropic", "/v1/messages"), ("ollama", "/api/chat"),
    ("openai-compatible", "/v1/chat/completions")])
async def test_one_normal_adapter_request_and_only_boolean_timing_results(monkeypatch, provider, path):
    raw, requests = fixture_image(), []
    monkeypatch.setattr(probe, "_challenge", lambda: (raw, ANSWER))
    async def endpoint(request):
        requests.append((request.path, dict(request.headers), await request.json()))
        return web.json_response(reply_for(provider))
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', endpoint)
    async with TestServer(app) as server:
        base = str(server.make_url('/' if provider == 'ollama' else '/v1')).rstrip('/')
        selected = VisionProvider({'provider': provider, 'model': 'synthetic-model',
                                   'base_url': base, 'api_key': KEY}, lab=True)
        report = await probe.probe_vision(selected)
    assert report['synthetic_image_matched'] is True and report['elapsed_ms'] >= 0
    assert report['model_identity_verified'] is False and report['quality_verified'] is False
    assert KEY not in json.dumps(report) and ANSWER not in json.dumps(report)
    assert len(requests) == 1
    actual, headers, body = requests[0]
    assert actual == path and headers.get('x-api-key' if provider == 'anthropic' else 'Authorization') == (
        KEY if provider == 'anthropic' else 'Bearer ' + KEY)
    assert 'Cookie' not in headers and 'x-ident' not in headers and 'Camera-MAC' not in headers
    assert body['model'] == 'synthetic-model'
    assert body.get('max_tokens', body.get('max_output_tokens', body.get('options', {}).get('num_predict'))) == 128
    if provider == 'ollama':
        message = body['messages'][0]
        assert ANSWER not in message['content'] and base64.b64decode(message['images'][0]) == raw
    else:
        record = body['input'][0] if provider == 'openai' else body['messages'][0]
        content = record['content']
        assert all(ANSWER not in part.get('text', '') for part in content)
        image = next(part for part in content if part['type'] in {'image', 'input_image', 'image_url'})
        encoded = (image['source']['data'] if provider == 'anthropic' else
                   image['image_url'] if provider == 'openai' else image['image_url']['url'])
        assert base64.b64decode(encoded.split(',')[-1]) == raw


async def test_a_complete_but_wrong_answer_is_not_called_a_modality_or_quality_result(monkeypatch):
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        await request.read()
        return web.json_response(reply_for('openai-compatible', KEY))
    app = web.Application()
    app.router.add_post('/v1/chat/completions', endpoint)
    async with TestServer(app) as server:
        selected = VisionProvider({'model': 'synthetic', 'base_url': str(server.make_url('/v1'))})
        report = await probe.probe_vision(selected)
    assert report['synthetic_image_matched'] is False and KEY not in json.dumps(report)


@pytest.mark.parametrize('kind', ['http', 'redirect', 'gzip', 'oversized', 'invalid', 'stream', 'incomplete'])
async def test_unusable_reply_never_retries_or_exposes_provider_content(monkeypatch, kind):
    requests = []
    monkeypatch.setattr(probe, '_challenge', lambda: (fixture_image(), ANSWER))
    async def endpoint(request):
        requests.append(request.path)
        await request.read()
        if kind == 'http':
            return web.json_response({'error': KEY}, status=503)
        if kind == 'redirect':
            raise web.HTTPFound('/must-not-be-followed')
        if kind == 'gzip':
            return web.Response(body=gzip.compress(json.dumps(reply_for('openai-compatible')).encode()),
                content_type='application/json', headers={'Content-Encoding': 'gzip'})
        if kind == 'oversized':
            return web.json_response({'padding': KEY * probe.MAX_REPLY_BYTES})
        if kind == 'invalid':
            return web.Response(text='{"choices":[],"choices":[]}', content_type='application/json')
        if kind == 'stream':
            return web.Response(text=KEY, content_type='text/event-stream')
        return web.json_response({'choices': [{'finish_reason': 'length', 'message': {'content': KEY}}]})
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', endpoint)
    async with TestServer(app) as server:
        selected = VisionProvider({'model': 'synthetic', 'base_url': str(server.make_url('/v1'))})
        with pytest.raises(probe.ProbeError, match='^probe_unavailable$') as error:
            await probe.probe_vision(selected)
    assert KEY not in str(error.value) and requests == ['/v1/chat/completions']


async def test_stalled_request_exits_with_one_uncertain_attempt(monkeypatch):
    requests = []
    monkeypatch.setattr(probe, 'TOTAL_SECONDS', 0.05)
    async def endpoint(request):
        requests.append(1)
        await request.read()
        await asyncio.sleep(0.12)
        return web.json_response(reply_for('openai-compatible'))
    app = web.Application()
    app.router.add_post('/v1/chat/completions', endpoint)
    async with TestServer(app) as server:
        selected = VisionProvider({'model': 'synthetic', 'base_url': str(server.make_url('/v1'))})
        with pytest.raises(probe.ProbeError):
            await probe.probe_vision(selected)
    assert requests == [1]


async def test_external_cancellation_is_preserved_without_retry_or_background_probe():
    entered, release, requests = asyncio.Event(), asyncio.Event(), []
    async def endpoint(request):
        requests.append(1)
        await request.read()
        entered.set()
        await release.wait()
        return web.json_response(reply_for('openai-compatible'))
    app = web.Application()
    app.router.add_post('/v1/chat/completions', endpoint)
    async with TestServer(app) as server:
        selected = VisionProvider({'model': 'synthetic', 'base_url': str(server.make_url('/v1'))})
        task = asyncio.create_task(probe.probe_vision(selected))
        await entered.wait()
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
    assert requests == [1]


async def test_address_refusal_is_proved_before_http_and_not_called_an_uncertain_charge(monkeypatch):
    requests = []
    async def endpoint(request):
        requests.append(request.path)
        return web.json_response(reply_for('openai-compatible'))
    app = web.Application()
    app.router.add_post('/v1/chat/completions', endpoint)
    async with TestServer(app) as server:
        monkeypatch.setattr(probe, 'ProviderConnector', lambda: ProviderConnector(
            resolver=Resolver(['127.0.0.1'])))
        selected = VisionProvider({'model': 'synthetic',
            'base_url': f'http://configured.example:{server.port}/v1'})
        with pytest.raises(probe.ProbeError, match='^probe_not_started$'):
            await probe.probe_vision(selected)
    assert requests == []


async def test_synthetic_preparation_failure_is_before_any_session_creation(monkeypatch):
    created = []
    def broken_challenge():
        raise OSError(KEY)
    monkeypatch.setattr(probe, '_challenge', broken_challenge)
    monkeypatch.setattr(probe.aiohttp, 'ClientSession', lambda *a, **kw: created.append(1))
    selected = VisionProvider({'model': 'synthetic', 'base_url': 'http://127.0.0.1:1/v1'})
    with pytest.raises(probe.ProbeError, match='^probe_not_started$') as error:
        await probe.probe_vision(selected)
    assert created == [] and KEY not in str(error.value)


def test_grants_are_one_use_context_bound_expiring_and_restart_invalidated():
    now = [0.0]
    grants = probe.ProbeGrants(clock=lambda: now[0])
    token = grants.issue('aikey', 'caption', 'revision', 'session')
    for context in [('aiport', 'caption', 'revision', 'session'),
                    ('aikey', 'detection', 'revision', 'session'),
                    ('aikey', 'caption', 'other', 'session'),
                    ('aikey', 'caption', 'revision', 'other')]:
        with pytest.raises(probe.ProbeError):
            grants.begin(token, *context)
    grants.begin(token, 'aikey', 'caption', 'revision', 'session')
    assert grants.busy
    grants.finish()
    now[0] = probe.COOLDOWN_SECONDS + 1
    with pytest.raises(probe.ProbeError):
        grants.begin(token, 'aikey', 'caption', 'revision', 'session')
    old = grants.issue('aikey', 'caption', 'revision', 'session')
    with pytest.raises(probe.ProbeError):
        probe.ProbeGrants().begin(old, 'aikey', 'caption', 'revision', 'session')
    now[0] += probe.GRANT_SECONDS
    with pytest.raises(probe.ProbeError):
        grants.begin(old, 'aikey', 'caption', 'revision', 'session')


def test_grant_capacity_and_busy_cooldown_do_not_consume_other_valid_forms():
    now = [0.0]
    grants = probe.ProbeGrants(clock=lambda: now[0])
    tokens = [grants.issue('aikey', 'caption', 'revision', 'session') for _ in range(probe.MAX_PENDING)]
    assert all(tokens) and grants.issue('aikey', 'caption', 'revision', 'session') is None
    grants.begin(tokens[0], 'aikey', 'caption', 'revision', 'session')
    with pytest.raises(probe.ProbeError, match='probe_busy'):
        grants.begin(tokens[1], 'aikey', 'caption', 'revision', 'session')
    grants.finish()
    with pytest.raises(probe.ProbeError, match='probe_busy'):
        grants.begin(tokens[1], 'aikey', 'caption', 'revision', 'session')
    now[0] = probe.COOLDOWN_SECONDS
    grants.begin(tokens[1], 'aikey', 'caption', 'revision', 'session')
    grants.finish()
    now[0] = probe.GRANT_SECONDS + 1
    assert grants.issue('aikey', 'caption', 'revision', 'session') is not None
    assert len(grants.pending) == 1

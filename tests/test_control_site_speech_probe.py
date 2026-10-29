"""Speech probe forms preserve auth, one-use grants and role/credential boundaries."""

import asyncio
import json

from aiohttp import web
import pytest

from aikey.admin_audit import AuditLog
from test_control_site_catalog import configured
from test_control_site_probe import form
from test_control_site_secrets import assert_private_page


@pytest.mark.parametrize("hallucination", [False, True])
async def test_speech_form_role_key_and_no_transcript_or_activation(tmp_path, hallucination):
    seen = []
    async def endpoint(request):
        seen.append((request.path, request.headers.get("Authorization"), await request.read()))
        return web.json_response({"text": "PRIVATE-TRANSCRIPT-CANARY" if hallucination else ""})
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    site.audit = AuditLog(tmp_path / "synthetic-audit.jsonl")
    try:
        fields, page = await form(client, role="speech")
        assert "one generated second of silent WAV audio" in page
        assert "Run one synthetic speech test" in page
        assert "Provider charges may apply" in page and "/speech/v1" in page
        before = path.read_bytes(), site.aiport.path.read_bytes()
        response = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert response.status == 200 and "Synthetic speech test" in body
        assert_private_page(response, body)
        assert "not voice recognition" in body and "no audio permission or configuration changed" in body
        assert "no transcript is shown" in body if hallucination else "no parsed speech" in body
        assert all(value not in body for value in (
            "PRIVATE-TRANSCRIPT-CANARY", "synthetic-speech-credential", "fictional-camera", "api_key_file"))
        assert len(seen) == 1 and seen[0][:2] == (
            "/speech/v1/audio/transcriptions", "Bearer synthetic-speech-credential")
        assert b"synthetic-silence.wav" in seen[0][2] and b"RIFF" in seen[0][2]
        assert before == (path.read_bytes(), site.aiport.path.read_bytes())
        site._probe_grants.next_at = 0
        replay = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        assert replay.status == 409 and len(seen) == 1
        assert "PRIVATE-TRANSCRIPT-CANARY" not in site.audit.path.read_text()
    finally:
        await client.close()
        await server.close()
        await upstream.close()


@pytest.mark.parametrize("failure", ["csrf", "origin", "revision", "grant", "acknowledge_cost",
    "role", "profile", "extra", "duplicate", "expired_session", "expired_grant", "cross_role"])
async def test_invalid_speech_grants_send_no_audio(tmp_path, failure):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        return web.json_response({"segments": []})
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client, role="speech")
        origin = site.origin
        if failure == "origin":
            origin = "http://untrusted.invalid"
        elif failure == "extra":
            fields["base_url"] = str(upstream.make_url("/unapproved"))
        elif failure == "duplicate":
            fields = list(fields.items()) + [("role", "caption")]
        elif failure == "expired_session":
            now = site.security._clock()
            site.security._clock = lambda: now + site.security.session_ttl + 1
        elif failure == "expired_grant":
            site._probe_grants.clock = lambda: 1e20
        elif failure == "cross_role":
            caption, _ = await form(client)
            fields["grant"] = caption["grant"]
        else:
            fields[failure] = "invalid"
        before = path.read_bytes(), site.aiport.path.read_bytes()
        response = await client.post("/provider-probe", data=fields, headers={"Origin": origin})
        assert response.status in {400, 403, 409} and seen == []
        assert before == (path.read_bytes(), site.aiport.path.read_bytes())
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_uncertain_speech_failure_does_not_leak_or_retry(tmp_path):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        return web.json_response({"error": "synthetic-speech-credential"}, status=503)
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client, role="speech")
        before = path.read_bytes()
        response = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        body = await response.text()
        assert "may have been charged" in body and "synthetic-speech-credential" not in body
        assert "no automatic retry" in body and path.read_bytes() == before
        site._probe_grants.next_at = 0
        replay = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        assert replay.status == 409 and len(seen) == 1
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_missing_speech_key_consumes_grant_without_any_http(tmp_path):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        return web.json_response({"segments": []})
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        fields, _ = await form(client, role="speech")
        before = path.read_bytes()
        key = path.parent / "speech-key"
        key.unlink()
        response = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        assert "No provider request was started" in await response.text()
        assert seen == [] and before == path.read_bytes()
        key.write_text("synthetic-speech-credential")
        key.chmod(0o600)
        site._probe_grants.next_at = 0
        replay = await client.post("/provider-probe", data=fields, headers={"Origin": site.origin})
        assert replay.status == 409 and seen == []
    finally:
        await client.close()
        await server.close()
        await upstream.close()


async def test_busy_speech_blocks_vision_and_stale_audio_is_not_current(tmp_path):
    entered, release, seen = asyncio.Event(), asyncio.Event(), []
    async def endpoint(request):
        seen.append(request.path)
        await request.read()
        entered.set()
        await release.wait()
        return web.json_response({"text": "PRIVATE-STALE-TRANSCRIPT"})
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        speech, _ = await form(client, role="speech")
        vision, _ = await form(client)
        task = asyncio.create_task(client.post("/provider-probe", data=speech,
                                              headers={"Origin": site.origin}))
        await asyncio.wait_for(entered.wait(), timeout=3)
        busy = await client.post("/provider-probe", data=vision, headers={"Origin": site.origin})
        assert busy.status == 429 and len(seen) == 1
        changed = json.loads(path.read_text())
        changed["speech_to_text"]["model"] = "different-fictional-model"
        path.write_text(json.dumps(changed) + "\n")
        expected = path.read_bytes()
        release.set()
        response = await task
        body = await response.text()
        assert "Synthetic result is stale" in body and "PRIVATE-STALE-TRANSCRIPT" not in body
        assert "parsed speech for" not in body and path.read_bytes() == expected
        assert len(seen) == 1 and not site._probe_grants.busy
    finally:
        release.set()
        await client.close()
        await server.close()
        await upstream.close()

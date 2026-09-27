"""Read-only control-site view of the continuous-caption preflight (#12)."""

import hashlib
import json
import time
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite
from test_control_site import fixture

PASSWORD = "synthetic-admin-passphrase"
CAMERA = "c" * 24


def jid(name):
    return hashlib.sha256(name.encode()).hexdigest()


def seed(key_state):
    jobs = key_state / "worker-jobs"
    jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    for name, state, age, operation in (("done", "completed", 2 * 86400, "describe"),
                                        ("unsure-1", "callback_uncertain", 600, "indexImages"),
                                        ("unsure-2", "callback_uncertain", 600, "indexImages")):
        (jobs / f"{jid(name)}.json").write_text(json.dumps({
            "jobId": jid(name), "fingerprint": jid(name + "f"), "state": state,
            "updatedAt": now - age, "operation": operation}))
    scopes = key_state / "worker-test-scopes"
    scopes.mkdir(exist_ok=True, mode=0o700)
    (scopes / f"{jid('p')}.json").write_text(json.dumps({
        "schema": 1, "camera_id": CAMERA, "permit_id": "synthetic-permit-7q2", "consumed_at": int(now),
        "job_id": jid("permit-job")}))


def digest(root):
    value = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


@pytest.fixture
async def signed_in(tmp_path):
    key_config, port_config = fixture(tmp_path)
    seed(key_config.parent)
    site = ControlSite(key_config, port_config, signing_key=b"p" * 32,
                       password_record=AdminSecurity.create_password_record(PASSWORD), port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"p" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        yield site, client, key_config
    finally:
        await client.close()
        await server.close()


async def login(site, client):
    await client.post("/login", data={"password": PASSWORD}, headers={"Origin": site.origin},
                      allow_redirects=False)


async def test_unauthenticated_requests_are_sent_to_sign_in(signed_in):
    site, client, _ = signed_in
    response = await client.get("/caption-preflight", allow_redirects=False)
    assert response.status == 303 and response.headers["Location"] == "/login"


async def test_the_signed_in_view_shows_codes_and_counts_only(signed_in):
    site, client, key_config = signed_in
    await login(site, client)
    before = digest(key_config.parent)
    assert "/caption-preflight" in await (await client.get("/")).text()
    response = await client.get("/caption-preflight")
    page = await response.text()
    assert response.headers["Cache-Control"] == "no-store"
    assert "<strong>Ready: no</strong>" in page
    for code in ("continuous_not_configured", "uncertain_callbacks_pending", "native_readback_missing"):
        assert f"<code>{code}</code>" in page
    assert "key_health_missing" not in page                        # not read by the site, not a blocker
    assert "2 uncertain callbacks" in page and "1 / 1" in page      # consumed / total permits
    assert "Due for rollover</th><td>1<" in page
    for secret in (CAMERA, jid("done"), jid("unsure-1"), jid("permit-job"), "synthetic-permit-7q2", str(key_config.parent)):
        assert secret not in page
    assert "<form" not in page.split("</h1>")[1]
    assert (await client.post("/caption-preflight", headers={"Origin": site.origin})).status == 405
    assert digest(key_config.parent) == before                      # read-only


async def test_malformed_state_is_reported_not_raised(signed_in):
    site, client, key_config = signed_in
    await login(site, client)
    (key_config.parent / "caption-budget.json").write_text("{not json")
    (key_config.parent / "worker-jobs" / f"{jid('broken')}.json").write_text("{broken")
    page = await (await client.get("/caption-preflight")).text()
    assert "<code>budget_journal_needs_review</code>" in page
    # An unreadable config gives a fixed reason, never a traceback or a path.
    original = key_config.read_bytes()
    key_config.unlink()
    key_config.symlink_to(key_config.parent / "elsewhere.json")
    page = await (await client.get("/caption-preflight")).text()
    assert "state_unreadable" in page and "Traceback" not in page and str(key_config) not in page
    key_config.unlink()
    key_config.write_bytes(original)

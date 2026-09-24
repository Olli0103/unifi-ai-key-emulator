"""A local browser can change provider settings without seeing secrets."""

import hashlib
import json
import ssl
import stat
import sys
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_security import AdminSecurity
from aikey.camera_inventory import InventoryError
from aikey.config import initialize
from aikey.control_site import ControlSite, _COOKIE, main
from aikey.tls import ensure_identity_certificate


def fixture(tmp_path):
    key_state = tmp_path / "key"
    key_config = key_state / "config.json"
    initialize(key_config, key_state, controller_host="127.0.0.1")
    port_state = tmp_path / "port"
    port_state.mkdir(mode=0o700)
    certificate, _ = ensure_identity_certificate(port_state, "2A1100F0A55E")
    ca = port_state / "controller-ca.pem"
    ca.write_bytes(certificate.read_bytes())
    ca.chmod(0o600)
    port_config = port_state / "config.json"
    port_config.write_text(json.dumps({
        "controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
        "mac": "2A1100F0A55E", "firmware_version": "5.1.12",
        "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(
            certificate.read_text())).hexdigest(),
        "paired_streams": [{"camera_mac": camera, "source_ip": "192.168.10.1",
                            "ffmpeg_path": sys.executable}
                           for camera in ("2A1122334455", "2A1122334456")],
        "live_pool_detector": {
            "checkpoint_path": str(port_state / "model.pth"),
            "checkpoint_sha256": "a" * 64, "threshold": 0.3,
            "smart_types": ["person"], "max_events_per_hour": 12},
    }) + "\n")
    port_config.chmod(0o600)
    return key_config, port_config


def test_generated_bootstrap_password_stays_private_and_is_not_printed(tmp_path, capsys):
    state = tmp_path / "admin"
    assert main(["init", "--state-dir", str(state), "--generate"]) == 0
    output = capsys.readouterr().out
    password = (state / "admin-bootstrap-password").read_text().strip()
    record = (state / "admin-password-record").read_text().strip()
    assert len(password) >= 32
    assert AdminSecurity.verify_password(password, record)
    assert password not in output
    assert stat.S_IMODE((state / "admin-bootstrap-password").stat().st_mode) == 0o600
    assert main(["init", "--state-dir", str(state), "--generate"]) == 2


@pytest.mark.asyncio
async def test_browser_login_provider_save_and_csrf_preserve_pairing(tmp_path):
    key_config, port_config = fixture(tmp_path)
    password = "synthetic-admin-passphrase"
    record = AdminSecurity.create_password_record(password)
    site = ControlSite(key_config, port_config, signing_key=b"s" * 32,
                       password_record=record, port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        response = await client.get("/", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        response = await client.post("/login", data={"password": password},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        assert _COOKIE in client.session.cookie_jar.filter_cookies(URL(site.origin))
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        csrf = site.security.csrf_token(cookie)
        page = await client.get("/")
        markup = await page.text()
        assert page.status == 200 and "AI Key" in markup and "AI Port" in markup
        assert "2 configured camera slots" in markup
        assert "Configured detector: local" in markup
        original_port = json.loads(port_config.read_text())
        data = {"csrf": csrf, "profile": "aiport",
                "revision": site.aiport.snapshot().revision,
                "provider": "ollama", "model": "synthetic-vision",
                "base_url": "http://127.0.0.1:11434",
                "max_output_tokens": "128", "threshold": "0.8",
                "smart_types": "person", "max_events_per_hour": "12",
                "max_requests_per_hour": "24"}
        rejected = await client.post("/provider", data={**data, "csrf": "wrong"},
                                     headers={"Origin": site.origin}, allow_redirects=False)
        assert rejected.status == 403
        assert json.loads(port_config.read_text()) == original_port
        saved = await client.post("/provider", data=data,
                                  headers={"Origin": site.origin}, allow_redirects=False)
        assert saved.status == 303
        updated = json.loads(port_config.read_text())
        assert updated["paired_streams"] == original_port["paired_streams"]
        assert updated["mac"] == original_port["mac"]
        assert updated["live_pool_detector"]["provider_config"]["model"] == "synthetic-vision"
        assert updated["live_pool_detector"]["inference_backend"] == "vision_api"
        assert site.aiport.snapshot().max_requests_per_hour == 24
        key_saved = await client.post("/provider", data={
            "csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision,
            "provider": "openai", "model": "gpt-6-luna",
            "base_url": "https://api.openai.com/v1", "allow_remote": "on",
            "max_output_tokens": "1024", "api_key": "synthetic-ai-key-secret",
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert key_saved.status == 303
        assert json.loads(key_config.read_text())["inference"]["provider"] == "openai"
        assert json.loads(key_config.read_text())["inference"]["max_output_tokens"] == 1024
        switched = await client.post("/provider", data={
            "csrf": csrf, "profile": "aikey", "revision": site.aikey.snapshot().revision,
            "provider": "ollama", "model": "synthetic-local-vision",
            "base_url": "http://127.0.0.1:11434", "max_output_tokens": "128",
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert switched.status == 303
        assert "api_key_file" not in json.loads(key_config.read_text())["inference"]
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_authenticated_camera_page_refreshes_inventory_and_marks_allowlist(tmp_path):
    key_config, port_config = fixture(tmp_path)
    requests = []
    unavailable = [False]

    async def inventory():
        requests.append(1)
        if unavailable[0]:
            raise InventoryError("Protect unavailable")
        return {"schema": "aikey-camera-preflight/1", "protect_version": "7.3.60",
                "fetched_at": 1800000000, "summary": {"total": 3}, "cameras": [
                    {"name": "<Büro>", "model": "UVC G5 Flex", "state": "CONNECTED",
                     "mac": "2A1122334455", "smart_detect_types": ["person"]},
                    {"name": "Esszimmer", "model": "UVC G4 Instant",
                     "state": "CONNECTED", "mac": "2A1122334499",
                     "smart_detect_types": []},
                    {"name": "Wohnzimmer", "model": "UVC G6 Instant",
                     "state": "CONNECTED", "mac": "2A1122334498",
                     "smart_detect_types": ["person"]}]}

    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"c" * 32,
                       password_record=AdminSecurity.create_password_record(password),
                       port=8765, inventory_loader=inventory)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        denied = await client.get("/cameras", allow_redirects=False)
        assert denied.status == 303 and requests == []
        await client.post("/login", data={"password": password},
                          headers={"Origin": site.origin}, allow_redirects=False)
        page = await client.get("/cameras")
        markup = await page.text()
        assert page.status == 200 and requests == [1]
        assert "&lt;Büro&gt;" in markup and "<Büro>" not in markup
        assert "Esszimmer" in markup and "Wohnzimmer" in markup
        assert "Configured for this AI Port" in markup
        assert "Not configured on this AI Port" in markup
        assert "Protect pairing and stream health are separate" in markup
        assert "Refresh" in markup
        await client.get("/cameras")
        assert requests == [1, 1]
        unavailable[0] = True
        failed = await (await client.get("/cameras")).text()
        assert "Camera inventory unavailable" in failed
        assert "Esszimmer" not in failed
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_openai_key_is_write_only_and_wrong_host_is_rejected(tmp_path):
    key_config, port_config = fixture(tmp_path)
    key_data = json.loads(key_config.read_text())
    key_data["runtime"]["state_dir"] = "/state"
    key_config.write_text(json.dumps(key_data) + "\n")
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"r" * 32,
                       password_record=AdminSecurity.create_password_record(password),
                       port=8765, aiport_runtime_state_dir=Path("/state"))
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"r" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        wrong = await client.get("/login", headers={"Host": "untrusted.example"})
        assert wrong.status == 403
        await client.post("/login", data={"password": password},
                          headers={"Origin": site.origin}, allow_redirects=False)
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        secret = "synthetic-private-api-key"
        response = await client.post("/provider", data={
            "csrf": site.security.csrf_token(cookie), "profile": "aiport",
            "revision": site.aiport.snapshot().revision,
            "provider": "openai", "model": "gpt-6-luna",
            "base_url": "https://api.openai.com/v1",
            "allow_remote": "on", "max_output_tokens": "256", "threshold": "0.8",
            "smart_types": "person", "max_events_per_hour": "12",
            "max_requests_per_hour": "24", "api_key": secret,
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 303
        saved = json.loads(port_config.read_text())
        key_path = saved["live_pool_detector"]["provider_config"]["api_key_file"]
        assert Path(key_path).parent == Path("/state")
        assert (port_config.parent / Path(key_path).name).read_text().strip() == secret
        assert site.aiport.snapshot().key_configured is True
        assert site.aiport.snapshot().allow_remote is True
        key_response = await client.post("/provider", data={
            "csrf": site.security.csrf_token(cookie), "profile": "aikey",
            "revision": site.aikey.snapshot().revision,
            "provider": "openai", "model": "gpt-6-luna",
            "base_url": "https://api.openai.com/v1",
            "allow_remote": "on", "max_output_tokens": "256",
            "api_key": "synthetic-key-profile-secret",
        }, headers={"Origin": site.origin}, allow_redirects=False)
        assert key_response.status == 303
        key_reference = json.loads(key_config.read_text())["inference"]["api_key_file"]
        assert Path(key_reference).parent == Path("/state")
        assert (key_config.parent / Path(key_reference).name).read_text().strip() == (
            "synthetic-key-profile-secret")
        markup = await (await client.get("/")).text()
        assert secret not in markup and key_path not in markup
        assert "synthetic-key-profile-secret" not in markup
        assert "Key reference configured: yes" in markup
    finally:
        await client.close()
        await server.close()

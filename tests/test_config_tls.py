import asyncio
import json
import ssl

import aiohttp
from aiohttp import web
import pytest

from aikey.config import ConfigError, hydrate_secrets, initialize, load_config, readiness
from aikey.tls import client_context, ensure_identity_certificate, server_context


def test_initialize_keeps_identity_private_and_refuses_overwrite(tmp_path):
    path = tmp_path / "config.json"
    state = tmp_path / "state"
    first = initialize(path, state)
    key = (state / "device.key").read_bytes()
    cert = (state / "device.crt").read_bytes()
    password = (state / "management-password").read_bytes()
    assert len(first["device"]["mac"]) == 12
    assert first["device"]["management_username"] == "ui"
    assert len(password.decode().strip()) >= 16
    assert int(first["device"]["mac"][:2], 16) & 3 == 2
    assert (state / "device.key").stat().st_mode & 0o077 == 0
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(ConfigError, match="already exists"):
        initialize(path, state)
    second = initialize(tmp_path / "second.json", state)
    assert first["device"]["mac"] == second["device"]["mac"]
    assert (state / "device.key").read_bytes() == key
    assert (state / "device.crt").read_bytes() == cert
    assert (state / "management-password").read_bytes() == password
    config = load_config(path)
    hydrated = hydrate_secrets(config)
    assert hydrated["device"]["management_password"] == password.decode().strip()
    assert "management_password" not in config["device"]
    report = readiness(config)
    assert report["native_compatibility"] == "needs_evidence"
    assert not report["ready_for_device_start"]
    assert password.decode().strip() not in json.dumps(report)


def test_existing_explicit_management_username_is_preserved(tmp_path):
    path = tmp_path / "config.json"
    config = initialize(path, tmp_path / "state")
    config["device"]["management_username"] = "existing-local-account"
    path.write_text(json.dumps(config))
    assert load_config(path)["device"]["management_username"] == "existing-local-account"
    assert hydrate_secrets(load_config(path))["device"]["management_username"] == "existing-local-account"


def test_incomplete_identity_is_not_regenerated(tmp_path):
    (tmp_path / "device.key").write_text("placeholder")
    with pytest.raises(ConfigError, match="Incomplete"):
        ensure_identity_certificate(tmp_path, "020000000001")


@pytest.mark.parametrize("section,key,value", [
    ("runtime", "mode", "typo"), ("runtime", "https_port", True),
    ("runtime", "https_port", 99999), ("runtime", "enable_http", True),
    ("device", "mac", "FFFFFFFFFFFF"), ("controller", "host", "https://example.com"),
    ("controller", "host", "host\r\nX-Token: other"),
    ("inference", "base_url", "http://user:secret@localhost/v1"),
    ("inference", "allow_remote", "false"), ("search", "enabled", "false"),
    ("embeddings", "allow_remote", "false"),
    ("embeddings", "allow_insecure_http", "false"),
    ("worker", "description_embeddings", "false"),
    ("worker", "max_video_duration_ms", False),
    ("worker", "max_video_duration_ms", 0),
    ("worker", "max_video_duration_ms", -1),
    ("worker", "max_video_duration_ms", "120000"),
])
def test_configuration_rejects_unsafe_or_ambiguous_values(tmp_path, section, key, value):
    path = tmp_path / "config.json"
    config = initialize(path, tmp_path / "state")
    config[section][key] = value
    path.write_text(json.dumps(config))
    with pytest.raises(ConfigError):
        load_config(path)


def test_basic_video_duration_has_explicit_default_and_accepts_override(tmp_path):
    path = tmp_path / "config.json"
    config = initialize(path, tmp_path / "state")
    assert config["worker"]["max_video_duration_ms"] == 120000
    config["worker"]["max_video_duration_ms"] = 60000
    path.write_text(json.dumps(config))
    assert load_config(path)["worker"]["max_video_duration_ms"] == 60000


def test_lab_cannot_bind_to_lan_or_call_lan_controller(tmp_path):
    path = tmp_path / "config.json"
    config = initialize(path, tmp_path / "state")
    config["runtime"].update(mode="lab", bind="0.0.0.0")
    path.write_text(json.dumps(config))
    with pytest.raises(ConfigError, match="loopback"):
        load_config(path)
    config["runtime"]["bind"] = "127.0.0.1"
    config["controller"]["host"] = "192.168.99.1"
    path.write_text(json.dumps(config))
    with pytest.raises(ConfigError, match="loopback"):
        load_config(path)


async def test_mutual_tls_and_rejection_of_untrusted_controller(tmp_path):
    controller_state = tmp_path / "controller"
    device_state = tmp_path / "device"
    wrong_state = tmp_path / "wrong"
    for path, mac in ((controller_state, "020000000001"), (device_state, "020000000002"),
                      (wrong_state, "020000000003")):
        ensure_identity_certificate(path, mac)
    context = server_context(controller_state)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(device_state / "device.crt")
    seen = []
    async def get(request):
        seen.append(request.transport.get_extra_info("ssl_object").getpeercert(binary_form=True))
        return web.json_response({"ok": True})
    app = web.Application()
    app.router.add_get("/", get)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = {"runtime": {"state_dir": str(device_state)},
              "controller": {"ca_file": str(controller_state / "device.crt"),
                             "verify_hostname": True}}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://127.0.0.1:{port}", ssl=client_context(config)) as response:
                assert await response.json() == {"ok": True}
            config["controller"]["ca_file"] = str(wrong_state / "device.crt")
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await session.get(f"https://127.0.0.1:{port}", ssl=client_context(config),
                                  headers={"x-token": "never-reaches-server"})
        assert len(seen) == 1
        assert seen[0]
    finally:
        await runner.cleanup()
        await asyncio.sleep(0)

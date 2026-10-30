"""Authenticated wizard HTTP flows with new fictional profiles; no real network/model."""

import asyncio
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import socket
import ssl
import threading

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey import control_site
from aikey.admin_security import AdminSecurity
from aikey.config import load_config
from aikey.first_run_site import FirstRunSite
from aikey.tls import ensure_identity_certificate

PASSWORD = "fictional-first-run-admin-password"
KEY = "synthetic-never-issued-first-run-vision-credential"


@pytest.fixture(autouse=True)
def forbid_external_and_tls_contacts(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Netless wizard attempted a TLS/controller/model contact")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    original = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        assert host in {"127.0.0.1", "::1", "localhost"}, "Wizard attempted external DNS"
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_only)


async def site_for(tmp_path, *, login=True, middleware=None, cleanup_event=None):
    directory = tmp_path / "new-key"
    site = FirstRunSite(
        directory,
        signing_key=b"w" * 32,
        password_record=AdminSecurity.create_password_record(PASSWORD),
        port=8765,
        audit_log=tmp_path / "wizard-audit.jsonl",
    )
    application = site.app()
    if middleware is not None:
        application.middlewares.append(middleware)
    if cleanup_event is not None:
        async def cleaning(_):
            cleanup_event.set()
        application.on_cleanup.insert(0, cleaning)
    server = TestServer(application, host="127.0.0.1")
    await server.start_server()
    site.origin = str(server.make_url("")).rstrip("/")
    site.security = AdminSecurity(b"w" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    if login:
        await client.post(
            "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
    return site, server, client, directory


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form":
            self.current = (attrs.get("action"), {})
        elif tag == "input" and self.current is not None and attrs.get("type") == "hidden":
            self.current[1][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            if self.current is not None:
                self.forms.append(self.current)
            self.current = None


async def form(client, action, channel=None):
    response = await client.get("/")
    markup = await response.text()
    parsed = Forms()
    parsed.feed(markup)
    fields = next(
        fields
        for route, fields in parsed.forms
        if route == action and (channel is None or fields.get("channel") == channel)
    )
    return fields, markup


def selection(hidden, **changes):
    return {
        **hidden,
        "controller": "192.0.2.1",
        "device_ip": "192.0.2.110",
        "bind": "",
        "https_port": "8080",
        "protect_version": "7.3.74",
        "control_profile": "ucp4",
        "controller_fingerprint": "",
        "search_fingerprint": "",
        "provider": "ollama",
        "model": "fictional-vision",
        "base_url": "",
        "api_key": "",
        "ffmpeg_path": "",
        "acknowledge_destination": "yes",
        **changes,
    }


def state_bytes(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


async def test_wizard_creates_private_profile_and_write_only_purpose_bound_key(tmp_path, caplog):
    site, server, client, directory = await site_for(tmp_path)
    try:
        fields, page = await form(client, "/create")
        assert "First-run AI Key preparation" in page and "No provider is called" in page
        assert "name='acknowledge_destination' value='yes' required" in page
        response = await client.post(
            "/create",
            data=selection(fields, provider="openai", api_key=KEY),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303 and response.headers["Location"] == "/"
        raw = (directory / "config.json").read_text()
        config = load_config(directory / "config.json")
        key_path = Path(config["inference"]["api_key_file"])
        assert key_path.parent == directory / "state" and key_path.read_text() == KEY
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700
        assert KEY not in raw and "api_key" not in config["inference"]
        assert all(config[k]["enabled"] is False for k in ("search", "database", "discovery"))
        assert not any(k in config["worker"] for k in ("continuous", "test_scope", "test_scopes"))
        identity = config["device"]["mac"]
        page = await (await client.get("/")).text()
        assert "Fresh profile prepared" in page and "No device was started or adopted" in page
        assert KEY not in page and str(key_path) not in page and identity not in page
        assert "api.openai.com/v1" in page and "not tested" in page
        assert KEY not in caplog.text and KEY not in site.audit.path.read_text()
        before = state_bytes(directory)
        repeated = await client.post(
            "/create", data=selection(fields), headers={"Origin": site.origin}
        )
        assert repeated.status == 409 and state_bytes(directory) == before
    finally:
        await client.close()
        await server.close()


async def test_signed_out_and_oversize_requests_have_no_filesystem_effect(tmp_path):
    site, server, client, directory = await site_for(tmp_path, login=False)
    try:
        response = await client.get("/", allow_redirects=False)
        assert response.status == 303 and response.headers["Location"] == "/login"
        response = await client.post("/create", data=selection({"csrf": "invalid"}),
                                     headers={"Origin": site.origin})
        assert response.status == 403 and not directory.exists()
        response = await client.post("/create", data=selection({"csrf": "invalid"}, api_key="a" * 20000),
                                     headers={"Origin": site.origin})
        assert response.status == 413 and not directory.exists()
    finally:
        await client.close()
        await server.close()


async def test_uncertain_key_write_retains_referenced_bytes_and_refuses_recreation(tmp_path, monkeypatch):
    from aikey import first_run_site
    site, server, client, directory = await site_for(tmp_path)
    original = first_run_site.atomic_private
    def write_then_fail(path, content):
        original(path, content)
        raise OSError("Synthetic post-commit uncertainty")
    monkeypatch.setattr(first_run_site, "atomic_private", write_then_fail)
    try:
        hidden, _ = await form(client, "/create")
        response = await client.post("/create", data=selection(hidden, provider="openai", api_key=KEY),
                                     headers={"Origin": site.origin})
        assert response.status == 400 and KEY not in await response.text()
        config = load_config(directory / "config.json")
        assert Path(config["inference"]["api_key_file"]).read_text() == KEY
        before = state_bytes(directory)
        status = await client.get("/")
        assert status.status == 409 and "Retained setup state" in await status.text()
        retry = await client.post("/create", data=selection(hidden), headers={"Origin": site.origin})
        assert retry.status == 409 and state_bytes(directory) == before
    finally:
        await client.close()
        await server.close()


async def test_cancelled_waiter_keeps_file_work_fenced_and_shutdown_drains_it(tmp_path, monkeypatch):
    from aikey import first_run_site
    entered, release = threading.Event(), threading.Event()
    cleanup = asyncio.Event()
    handler = asyncio.get_running_loop().create_future()
    @web.middleware
    async def observe(request, next_handler):
        if request.path == "/create" and not handler.done():
            handler.set_result(asyncio.current_task())
        return await next_handler(request)
    # aiohttp supplies this keyword by its public middleware contract.
    async def middleware(request, handler):
        return await observe(request, handler)
    middleware = web.middleware(middleware)
    original = first_run_site.prepare
    def held(*args):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Synthetic file gate deadline")
        return original(*args)
    monkeypatch.setattr(first_run_site, "prepare", held)
    site, server, client, directory = await site_for(tmp_path, middleware=middleware, cleanup_event=cleanup)
    try:
        fields, _ = await form(client, "/create")
        request_task = asyncio.create_task(client.post("/create", data=selection(fields),
            headers={"Origin": site.origin}, allow_redirects=False))
        assert await asyncio.to_thread(entered.wait, 3)
        (await handler).cancel()
        await asyncio.gather(request_task, return_exceptions=True)
        assert site._operations and not site.prepared and not directory.exists()
        refused = await client.post("/create", data=selection(fields), headers={"Origin": site.origin})
        assert refused.status == 409
        closing = asyncio.create_task(server.close())
        await asyncio.wait_for(cleanup.wait(), 3)
        assert not closing.done() and site._operations
        release.set()
        await asyncio.wait_for(closing, 3)
        assert not site._operations and not site.prepared
        assert (directory / "config.json").is_file()
        with pytest.raises(ValueError, match="already exists"):
            FirstRunSite(directory, signing_key=b"w" * 32, password_record="unused", port=8765)
    finally:
        release.set()
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "failure", ["csrf", "unicode_csrf", "origin", "host", "ack", "duplicate", "extra", "expired"]
)
async def test_unauthorized_or_malformed_creation_never_creates_state(tmp_path, failure):
    site, server, client, directory = await site_for(tmp_path)
    try:
        hidden, _ = await form(client, "/create")
        fields = selection(hidden, provider="openai", api_key=KEY)
        headers = {"Origin": site.origin}
        if failure == "csrf":
            fields["csrf"] = "invalid"
        if failure == "unicode_csrf":
            fields["csrf"] = "ä" * 43
        if failure == "origin":
            headers["Origin"] = "https://untrusted.invalid"
        if failure == "host":
            headers["Host"] = "untrusted.invalid"
        if failure == "ack":
            fields["acknowledge_destination"] = "no"
        if failure == "duplicate":
            fields = list(fields.items()) + [("provider", "ollama")]
        if failure == "extra":
            fields["directory"] = str(tmp_path / "unapproved-target")
        if failure == "expired":
            now = site.security._clock()
            site.security._clock = lambda: now + site.security.session_ttl + 1
        response = await client.post("/create", data=fields, headers=headers)
        assert response.status in {400, 403} and not directory.exists()
        assert KEY not in await response.text()
        assert not (tmp_path / "unapproved-target").exists()
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "invalid"},
        {"provider": "openai"},
        {"provider": "openai", "api_key": KEY, "base_url": "https://wrong.invalid/v1"},
        {"model": " "},
        {"device_ip": "bad"},
        {"control_profile": "device-service"},
        {"controller_fingerprint": "bad"},
        {"search_fingerprint": "bad"},
        {"https_port": "65536"},
        {"https_port": "1e3"},
        {"ffmpeg_path": "relative"},
        {"base_url": "http://192.168.50.2:11434", "allow_remote": "yes"},
        {"api_key": "not a key"},
        {"api_key": "nonascii-ä"},
        {"pin_only_hostname": "yes"},
    ],
)
async def test_invalid_settings_do_not_generate_identity_or_store_key(tmp_path, changes):
    site, server, client, directory = await site_for(tmp_path)
    try:
        fields, _ = await form(client, "/create")
        response = await client.post(
            "/create", data=selection(fields, **changes), headers={"Origin": site.origin}
        )
        assert response.status == 400 and not directory.exists()
        assert KEY not in await response.text()
    finally:
        await client.close()
        await server.close()


async def test_concurrent_forms_admit_one_identity_and_no_operational_routes(tmp_path):
    site, server, client, directory = await site_for(tmp_path)
    try:
        fields, _ = await form(client, "/create")
        responses = await asyncio.gather(
            *(
                client.post(
                    "/create",
                    data=selection(fields),
                    headers={"Origin": site.origin},
                    allow_redirects=False,
                )
                for _ in range(2)
            )
        )
        assert sorted(r.status for r in responses) == [303, 409]
        before = state_bytes(directory)
        for route in ("/run", "/adopt", "/reset", "/provider-probe", "/model-catalog", "/provider"):
            assert (
                await client.post(route, data=fields, headers={"Origin": site.origin})
            ).status == 404
        assert state_bytes(directory) == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("kind", ["empty", "partial", "file", "link", "dangling"])
def test_existing_targets_are_refused_before_server_initialization(tmp_path, kind):
    directory, target = tmp_path / "existing", tmp_path / "target"
    if kind in {"empty", "partial"}:
        directory.mkdir()
        if kind == "partial":
            (directory / "identity.json").write_text("retained")
    elif kind == "file":
        directory.write_text("retained")
    else:
        if kind == "link":
            target.mkdir()
        directory.symlink_to(target)
    before = state_bytes(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        FirstRunSite(directory, signing_key=b"w" * 32, password_record="unused", port=8765)
    assert state_bytes(tmp_path) == before


@pytest.fixture
def certificates(tmp_path):
    result = []
    for name in ("control", "search"):
        directory = tmp_path / ("synthetic-cert-" + name)
        directory.mkdir()
        cert, _ = ensure_identity_certificate(directory, "02000000ABCD")
        pem = cert.read_text()
        result.append((pem, hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()))
    return result


async def test_offline_distinct_trust_imports_pin_cooldown_replay_and_config_unchanged(
    tmp_path, certificates
):
    site, server, client, directory = await site_for(tmp_path)
    (control_pem, control_pin), (search_pem, search_pin) = certificates
    try:
        fields, _ = await form(client, "/create")
        response = await client.post(
            "/create",
            data=selection(
                fields,
                control_profile="device-service",
                controller_fingerprint=control_pin,
                search_fingerprint=search_pin,
                pin_only_hostname="yes",
            ),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        before = (directory / "config.json").read_bytes()
        first, _ = await form(client, "/trust", "control")
        second, _ = await form(client, "/trust", "search")
        first.update(fingerprint=control_pin, certificate=control_pem, acknowledge_import="yes")
        second.update(fingerprint=search_pin, certificate=search_pem, acknowledge_import="yes")
        assert (
            await client.post(
                "/trust", data=first, headers={"Origin": site.origin}, allow_redirects=False
            )
        ).status == 303
        assert (
            await client.post("/trust", data=second, headers={"Origin": site.origin})
        ).status == 409
        now = site._probe_grants.clock()
        site._probe_grants.clock = lambda: now + 31
        assert (
            await client.post(
                "/trust", data=second, headers={"Origin": site.origin}, allow_redirects=False
            )
        ).status == 303
        assert (directory / "state" / "controller-ca.pem").read_text() == control_pem
        assert (directory / "state" / "controller-search-ca.pem").read_text() == search_pem
        assert (directory / "config.json").read_bytes() == before
        now = site._probe_grants.clock()
        site._probe_grants.clock = lambda: now + 31
        assert (
            await client.post("/trust", data=first, headers={"Origin": site.origin})
        ).status == 409
        assert (directory / "config.json").read_bytes() == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_pin",
        "wrong_certificate",
        "private_key",
        "chain",
        "extra_field",
        "stale",
        "unicode_revision",
        "expired",
        "cross_channel",
    ],
)
async def test_bad_trust_never_installs_or_exposes_supplied_material(tmp_path, certificates, fault):
    site, server, client, directory = await site_for(tmp_path)
    (pem, pin), (other_pem, _) = certificates
    try:
        hidden, _ = await form(client, "/create")
        assert (
            await client.post(
                "/create",
                data=selection(hidden, controller_fingerprint=pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 303
        fields, _ = await form(client, "/trust", "control")
        fields.update(fingerprint=pin, certificate=pem, acknowledge_import="yes")
        if fault == "wrong_pin":
            fields["fingerprint"] = "00" * 32
        if fault == "wrong_certificate":
            fields["certificate"] = other_pem
        if fault == "private_key":
            fields["certificate"] = (
                "-----BEGIN " + "PRIVATE KEY-----\nPRIVATE-CANARY\n-----END " + "PRIVATE KEY-----"
            )
        if fault == "chain":
            fields["certificate"] += other_pem
        if fault == "extra_field":
            fields["output"] = str(tmp_path / "unapproved.pem")
        if fault == "stale":
            fields["revision"] = "00" * 32
        if fault == "unicode_revision":
            fields["revision"] = "ä" * 64
        if fault == "expired":
            site._probe_grants.clock = lambda: 1e20
        if fault == "cross_channel":
            fields["channel"] = "search"
        before = state_bytes(directory)
        response = await client.post("/trust", data=fields, headers={"Origin": site.origin})
        assert response.status in {400, 409} and state_bytes(directory) == before
        assert "PRIVATE-CANARY" not in await response.text()
        assert not (tmp_path / "unapproved.pem").exists()
    finally:
        await client.close()
        await server.close()


def test_real_cli_uses_existing_admin_auth_and_does_not_start_device(tmp_path, monkeypatch, capsys):
    state, target = tmp_path / "admin", tmp_path / "new-key"
    assert control_site.main(["init", "--state-dir", str(state), "--generate"]) == 0
    capsys.readouterr()
    seen = []

    async def no_listener(site, port):
        assert isinstance(site, FirstRunSite)
        seen.append((site.directory, port))

    monkeypatch.setattr(control_site, "_run", no_listener)
    assert control_site.main(["setup", "--state-dir", str(state), "--directory", str(target)]) == 0
    assert seen == [(target, 8765)] and not target.exists()
    assert json.loads(capsys.readouterr().out)["device_started"] is False

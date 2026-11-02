"""Fresh, fictional Port preparation; no controller/provider/model access."""

import asyncio
import json
import threading

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from multidict import MultiDict
import pytest

from aikey.admin_security import AdminSecurity
from aikey.aiport_candidate import load_config
from aikey.first_run_port_site import FirstRunPortSite
from test_aiport_instance_state import fixture
from test_first_run_site import PASSWORD, form, state_bytes
from test_first_run_site import forbid_external_and_tls_contacts as forbid_external_and_tls_contacts


async def site_for(tmp_path, *, source=None, login=True, middleware=None, cleanup_event=None):
    if source is None:
        plan, cert, pin, root = fixture(tmp_path)
        plan_path = tmp_path / "reviewed-plan.json"
        plan_path.write_text(json.dumps(plan))
        plan_path.chmod(0o600)
        source = plan_path, cert, pin, root
    plan_path, cert, pin, root = source
    site = FirstRunPortSite(
        root / "slot-1",
        plan=plan_path,
        slot=1,
        controller_cert_file=cert,
        signing_key=b"w" * 32,
        password_record=AdminSecurity.create_password_record(PASSWORD),
        port=8765,
        audit_log=tmp_path / "port-wizard-audit.jsonl",
    )
    app = site.app()
    if middleware is not None:
        app.middlewares.append(middleware)
    if cleanup_event is not None:

        async def cleaning(_):
            cleanup_event.set()

        app.on_cleanup.insert(0, cleaning)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    site.origin = str(server.make_url("")).rstrip("/")
    site.security = AdminSecurity(b"w" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    if login:
        response = await client.post(
            "/login",
            data={"password": PASSWORD},
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
    return site, server, client, source


def selection(hidden, pin, **changes):
    return {
        **hidden,
        "controller": "192.168.10.1",
        "controller_pin": pin,
        "acknowledge_trust": "yes",
        "acknowledge_preparation": "yes",
        **changes,
    }


async def test_private_passive_profile_and_separate_port_audit(tmp_path, caplog):
    site, server, client, source = await site_for(tmp_path)
    plan_path, cert, pin, root = source
    inputs = plan_path.read_bytes(), cert.read_bytes()
    try:
        hidden, page = await form(client, "/create")
        assert "First-run AI Port preparation" in page
        assert "not a camera-permission grant" in page and "ONVIF slots are unsupported" in page
        assert "name='api_key'" not in page and "name='plan'" not in page
        response = await client.post(
            "/create",
            data=selection(hidden, pin),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 303
        directory = root / "slot-1"
        raw = json.loads((directory / "config.json").read_bytes())
        assert set(raw) == {
            "controller_ip",
            "device_ip",
            "controller_pin",
            "firmware_version",
            "mac",
        }
        config = load_config(directory / "config.json", check_decoder_executable=False)
        assert config["device_ip"] == "192.168.10.20"
        assert directory.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
        page = await (await client.get("/")).text()
        assert "Fresh AI Port files prepared" in page
        assert "Current runtime/adoption status was not queried" in page
        private_key = (directory / "device.key").read_text()
        assert private_key not in page and private_key not in caplog.text
        before = state_bytes(directory)
        assert (
            await client.post(
                "/create",
                data=selection(hidden, pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 409
        assert state_bytes(directory) == before
        assert (
            await client.post(
                "/trust", data=selection(hidden, pin), headers={"Origin": site.origin}
            )
        ).status == 404
        audit = [
            json.loads(line)
            for line in (tmp_path / "port-wizard-audit.jsonl").read_text().splitlines()
        ]
        created = [x for x in audit if x["action"] == "first_run_create"]
        assert created and all(x["profile"] == "aiport" for x in created)
        assert private_key not in json.dumps(audit)
        assert (plan_path.read_bytes(), cert.read_bytes()) == inputs
        logout, _ = await form(client, "/logout")
        assert (
            await client.post(
                "/logout", data=logout, headers={"Origin": site.origin}, allow_redirects=False
            )
        ).status == 303
        assert (await client.get("/", allow_redirects=False)).status == 303
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "change", ["pin", "controller", "trust_ack", "fresh_ack", "revision", "extra", "duplicate"]
)
async def test_refusals_create_no_identity(tmp_path, change):
    site, server, client, source = await site_for(tmp_path)
    _, _, pin, root = source
    try:
        hidden, _ = await form(client, "/create")
        fields = MultiDict(selection(hidden, pin))
        if change == "pin":
            fields["controller_pin"] = "0" * 64
        elif change == "controller":
            fields["controller"] = "127.0.0.1"
        elif change == "trust_ack":
            fields["acknowledge_trust"] = "no"
        elif change == "fresh_ack":
            fields["acknowledge_preparation"] = "no"
        elif change == "revision":
            fields["revision"] = "0" * 64
        elif change == "extra":
            fields["plan"] = "/unread/private-plan"
        else:
            fields.add("controller", "192.168.10.2")
        response = await client.post(
            "/create", data=fields, headers={"Origin": site.origin}, allow_redirects=False
        )
        assert response.status in {400, 409}
        assert list(root.iterdir()) == [] and not site.prepared
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("boundary", ["anonymous", "csrf", "origin"])
async def test_existing_auth_boundary_precedes_identity_writes(tmp_path, boundary):
    site, server, client, source = await site_for(tmp_path, login=boundary != "anonymous")
    _, _, pin, root = source
    try:
        if boundary == "anonymous":
            assert (await client.get("/", allow_redirects=False)).status == 303
            fields = {"csrf": "fictional", "revision": "0" * 64}
        else:
            fields, _ = await form(client, "/create")
        fields = selection(fields, pin)
        if boundary == "csrf":
            fields["csrf"] = "fictional-wrong"
        origin = "https://fictional.invalid" if boundary == "origin" else site.origin
        assert (
            await client.post(
                "/create", data=fields, headers={"Origin": origin}, allow_redirects=False
            )
        ).status == 403
        assert list(root.iterdir()) == []
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize("which", ["plan", "certificate"])
async def test_input_change_invalidates_existing_form(tmp_path, which):
    site, server, client, source = await site_for(tmp_path)
    plan_path, cert, pin, root = source
    try:
        hidden, _ = await form(client, "/create")
        selected = plan_path if which == "plan" else cert
        selected.write_bytes(selected.read_bytes() + b"\n")
        response = await client.post(
            "/create",
            data=selection(hidden, pin),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 409 and list(root.iterdir()) == []
    finally:
        await client.close()
        await server.close()


async def test_two_http_authorities_only_one_fresh_identity(tmp_path):
    site, server, client, source = await site_for(tmp_path)
    other, server2, client2, _ = await site_for(tmp_path, source=source)
    _, _, pin, root = source
    try:
        hidden, _ = await form(client, "/create")
        hidden2, _ = await form(client2, "/create")
        responses = await asyncio.gather(
            *[
                c.post(
                    "/create",
                    data=selection(f, pin),
                    headers={"Origin": s.origin},
                    allow_redirects=False,
                )
                for c, f, s in [(client, hidden, site), (client2, hidden2, other)]
            ]
        )
        statuses = [r.status for r in responses]
        assert statuses.count(303) == 1 and any(x in {400, 409} for x in statuses)
        assert len(list(root.iterdir())) == 1
        assert sum(s.prepared for s in [site, other]) == 1
        assert not site._operations and not other._operations
    finally:
        await client.close()
        await client2.close()
        await server.close()
        await server2.close()


async def test_changed_prepared_state_is_not_reported_as_unchanged(tmp_path):
    site, server, client, source = await site_for(tmp_path)
    _, _, pin, root = source
    try:
        hidden, _ = await form(client, "/create")
        assert (
            await client.post(
                "/create",
                data=selection(hidden, pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 303
        config = root / "slot-1/config.json"
        config.write_bytes(config.read_bytes() + b"\n")
        before = state_bytes(root / "slot-1")
        response = await client.get("/")
        assert response.status == 409
        assert "Prepared state changed or unavailable" in await response.text()
        assert state_bytes(root / "slot-1") == before
    finally:
        await client.close()
        await server.close()


@pytest.mark.parametrize(
    "change", ["existing", "symlink_plan", "public_plan", "duplicate_json", "onvif"]
)
def test_startup_refusals_never_prepare_a_profile(tmp_path, change):
    plan, cert, _, root = fixture(tmp_path)
    plan_path = tmp_path / "plan.json"
    if change == "onvif":
        plan["instances"][0]["source_kind"] = "onvif"
    raw = json.dumps(plan)
    if change == "duplicate_json":
        raw = raw.replace("{", '{"schema":"duplicate",', 1)
    plan_path.write_text(raw)
    plan_path.chmod(0o600)
    if change == "public_plan":
        plan_path.chmod(0o644)
    if change == "symlink_plan":
        alias = tmp_path / "alias.json"
        alias.symlink_to(plan_path)
        plan_path = alias
    directory = root / "slot-1"
    if change == "existing":
        directory.mkdir(mode=0o700)
    with pytest.raises((ValueError, OSError)):
        FirstRunPortSite(
            directory,
            plan=plan_path,
            slot=1,
            controller_cert_file=cert,
            signing_key=b"w" * 32,
            password_record=AdminSecurity.create_password_record(PASSWORD),
            port=8765,
        )
    assert not directory.exists() or list(directory.iterdir()) == []


def test_revision_is_bound_to_the_selected_new_directory(tmp_path):
    plan, cert, _, root = fixture(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    plan_path.chmod(0o600)
    options = dict(
        plan=plan_path,
        slot=1,
        controller_cert_file=cert,
        signing_key=b"w" * 32,
        password_record=AdminSecurity.create_password_record(PASSWORD),
        port=8765,
    )
    first = FirstRunPortSite(root / "first", **options)
    second = FirstRunPortSite(root / "second", **options)
    assert first._inputs()[0] != second._inputs()[0]
    assert list(root.iterdir()) == []


async def test_cancelled_port_waiter_retains_operation_and_shutdown_drains(tmp_path, monkeypatch):
    from aikey import first_run_port_site as module

    entered, release = threading.Event(), threading.Event()
    cleanup = asyncio.Event()
    captured = asyncio.get_running_loop().create_future()

    @web.middleware
    async def observe(request, handler):
        if request.path == "/create" and not captured.done():
            captured.set_result(asyncio.current_task())
        return await handler(request)

    original = module.provision_slot

    def held(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Fictional file gate expired")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "provision_slot", held)
    site, server, client, source = await site_for(
        tmp_path, middleware=observe, cleanup_event=cleanup
    )
    plan, cert, pin, root = source
    try:
        hidden, _ = await form(client, "/create")
        waiting = asyncio.create_task(
            client.post(
                "/create",
                data=selection(hidden, pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        )
        assert await asyncio.to_thread(entered.wait, 3)
        (await captured).cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        assert site._operations and not site.prepared and not site.directory.exists()
        assert (
            await client.post(
                "/create",
                data=selection(hidden, pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 409
        closing = asyncio.create_task(server.close())
        await asyncio.wait_for(cleanup.wait(), 3)
        assert not closing.done() and site._operations
        release.set()
        await asyncio.wait_for(closing, 3)
        assert not site._operations and not site.prepared
        assert (root / "slot-1/config.json").is_file()
        with pytest.raises(ValueError, match="already exists"):
            FirstRunPortSite(
                root / "slot-1",
                plan=plan,
                slot=1,
                controller_cert_file=cert,
                signing_key=b"w" * 32,
                password_record="unused",
                port=8765,
            )
    finally:
        release.set()
        await client.close()
        await server.close()


async def test_post_creation_error_preserves_partial_state_and_refuses_retry(tmp_path, monkeypatch):
    site, server, client, source = await site_for(tmp_path)
    _, _, pin, root = source

    def uncertain():
        raise OSError("fictional private post-write failure")

    monkeypatch.setattr(site, "_snapshot", uncertain)
    try:
        hidden, _ = await form(client, "/create")
        response = await client.post(
            "/create",
            data=selection(hidden, pin),
            headers={"Origin": site.origin},
            allow_redirects=False,
        )
        assert response.status == 400 and not site.prepared
        before = state_bytes(root / "slot-1")
        assert before and "fictional private post-write failure" not in await response.text()
        status = await client.get("/")
        assert status.status == 409 and "Retained setup state" in await status.text()
        assert (
            await client.post(
                "/create",
                data=selection(hidden, pin),
                headers={"Origin": site.origin},
                allow_redirects=False,
            )
        ).status == 409
        assert state_bytes(root / "slot-1") == before
    finally:
        await client.close()
        await server.close()


def test_actual_cli_selects_port_only_preparation_without_creating_identity(
    tmp_path, monkeypatch, capsys
):
    from aikey import control_site

    plan, cert, _, root = fixture(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    plan_path.chmod(0o600)
    authority = tmp_path / "admin"
    assert control_site.main(["init", "--state-dir", str(authority), "--generate"]) == 0
    capsys.readouterr()
    before = state_bytes(authority)
    seen = []

    async def observed(site, port):
        assert isinstance(site, FirstRunPortSite) and site.slot == 1 and port == 8765
        assert not site.directory.exists()
        seen.append(site)

    monkeypatch.setattr(control_site, "_run", observed)
    assert (
        control_site.main(
            [
                "setup-port",
                "--state-dir",
                str(authority),
                "--directory",
                str(root / "slot-1"),
                "--plan",
                str(plan_path),
                "--slot",
                "1",
                "--controller-cert-file",
                str(cert),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["profile"] == "aiport" and output["device_started"] is False
    assert output["cameras_paired"] is False and len(seen) == 1
    assert list(root.iterdir()) == [] and state_bytes(authority) == before


def test_existing_target_refuses_before_selected_input_reads(tmp_path, monkeypatch):
    from aikey import first_run_port_site as module

    directory = tmp_path / "existing"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(module, "_private_bytes", lambda *_: pytest.fail("selected input read"))
    with pytest.raises(ValueError, match="already exists"):
        FirstRunPortSite(
            directory,
            plan=tmp_path / "not-read-plan",
            slot=1,
            controller_cert_file=tmp_path / "not-read-cert",
            signing_key=b"w" * 32,
            password_record="unused",
            port=8765,
        )
    assert list(directory.iterdir()) == []

"""Synthetic camera inventory and pinned local API transport tests."""

from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import ssl

from aiohttp import web
import pytest

from aikey import camera_inventory
from aikey.camera_inventory import InventoryError, fetch_inventory, parse_cameras, render_html
from aikey.tls import ensure_identity_certificate, server_context


def camera(number=1, *, name="Synthetic camera", state="CONNECTED", smart=("person",)):
    return {"id": f"{number:024x}", "modelKey": "camera", "state": state,
            "name": name, "type": "Synthetic model",
            "featureFlags": {"smartDetectTypes": list(smart), "smartDetectAudioTypes": []}}


def private_file(path: Path, content: str | bytes):
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(0o600)
    return path


@asynccontextmanager
async def synthetic_protect(tmp_path, monkeypatch, *, meta_version="7.3.60",
                            meta_status=200, redirect=False, rows=None):
    ensure_identity_certificate(tmp_path, "020000000001")
    cert = tmp_path / "device.crt"
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    seen = []
    app = web.Application()

    async def meta(request):
        seen.append((request.path, request.headers.get("X-API-Key")))
        if redirect:
            raise web.HTTPFound("/leak")
        if meta_status != 200:
            return web.Response(status=meta_status)
        return web.json_response({"applicationVersion": meta_version})

    async def cameras(request):
        seen.append((request.path, request.headers.get("X-API-Key")))
        return web.json_response([camera()] if rows is None else rows)

    async def leak(request):
        seen.append((request.path, request.headers.get("X-API-Key")))
        return web.Response(text="unexpected")

    app.router.add_get("/proxy/protect/integration/v1/meta/info", meta)
    app.router.add_get("/proxy/protect/integration/v1/cameras", cameras)
    app.router.add_get("/leak", leak)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context(tmp_path))
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(camera_inventory, "_INTEGRATION_PORT", port)
    key = private_file(tmp_path / "api-key", "synthetic-api-key\n")
    trust = private_file(tmp_path / "web-trust.json", json.dumps({
        "host": "127.0.0.1", "port": port, "sha256": hashlib.sha256(der).hexdigest()}))
    ca = private_file(tmp_path / "web-cert.pem", cert.read_bytes())
    try:
        yield {"seen": seen, "key": key, "trust": trust, "ca": ca}
    finally:
        await runner.cleanup()


def test_inventory_add_remove_rename_offline_and_legacy_classification():
    first = parse_cameras([camera(1, name="First"), camera(2, name="Legacy", smart=())])
    assert {item.processing_class for item in first} == {
        "smart_event_candidate", "legacy_ingress_needed"}
    second = parse_cameras([camera(1, name="Renamed", state="DISCONNECTED"), camera(3)])
    assert [item.name for item in second] == ["Renamed", "Synthetic camera"]
    assert second[0].processing_class == "offline"
    assert {item.id for item in second}.isdisjoint({first[1].id})


def test_live_observed_audio_flag_is_preserved_without_enabling_processing():
    row = camera()
    row["featureFlags"]["smartDetectAudioTypes"] = ["smoke_cmonx"]
    parsed = parse_cameras([row])[0]
    assert parsed.audio_detect_types == ("smoke_cmonx",)
    assert parsed.processing_class == "smart_event_candidate"


def test_static_report_escapes_camera_names_and_has_no_script():
    row = camera(name='<img src=x onerror="alert(1)">')
    item = parse_cameras([row])[0]
    report = {"protect_version": "7.3.60", "fetched_at": 1700000000,
              "summary": {"total": 1, "smart_event_candidates": 1,
                          "legacy_ingress_needed": 0, "offline": 0},
              "cameras": [item.public()]}
    page = render_html(report)
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page
    assert "<img" not in page and "<script" not in page
    assert "default-src 'none'" in page


@pytest.mark.parametrize("rows", [
    [camera(1), camera(1)],
    [{**camera(1), "state": "UNKNOWN"}],
    [{**camera(1), "featureFlags": {"smartDetectTypes": ["person"]}}],
    [{**camera(1), "featureFlags": {"smartDetectTypes": ["futureClass"],
                                   "smartDetectAudioTypes": []}}],
    [{**camera(1), "id": "not-an-id"}],
])
def test_invalid_inventory_fails_as_a_whole(rows):
    with pytest.raises(InventoryError):
        parse_cameras(rows)


@pytest.mark.asyncio
async def test_pinned_read_only_inventory_has_no_processing_scope(tmp_path, monkeypatch):
    async with synthetic_protect(tmp_path, monkeypatch, rows=[camera(1), camera(2, smart=())]) as env:
        report = await fetch_inventory("127.0.0.1", api_key_file=env["key"],
                                       trust_file=env["trust"], cert_file=env["ca"])
        assert report["protect_version"] == "7.3.60"
        assert report["summary"] == {"total": 2, "smart_event_candidates": 1,
                                     "legacy_ingress_needed": 1, "offline": 0}
        assert report["processing_enabled"] is False
        assert "synthetic-api-key" not in json.dumps(report)
        assert [path for path, _ in env["seen"]] == [
            "/proxy/protect/integration/v1/meta/info",
            "/proxy/protect/integration/v1/cameras"]
        assert all(header == "synthetic-api-key" for _, header in env["seen"])


@pytest.mark.asyncio
async def test_wrong_pin_sends_no_api_key(tmp_path, monkeypatch):
    async with synthetic_protect(tmp_path, monkeypatch) as env:
        private_file(env["trust"], json.dumps({"host": "127.0.0.1",
            "port": camera_inventory._INTEGRATION_PORT, "sha256": "0" * 64}))
        with pytest.raises(InventoryError):
            await fetch_inventory("127.0.0.1", api_key_file=env["key"],
                                  trust_file=env["trust"], cert_file=env["ca"])
        assert env["seen"] == []


@pytest.mark.asyncio
async def test_unexpected_live_leaf_sends_no_api_key(tmp_path, monkeypatch):
    async with synthetic_protect(tmp_path, monkeypatch) as env:
        other = tmp_path / "other"
        other.mkdir()
        ensure_identity_certificate(other, "020000000002")
        pem = (other / "device.crt").read_text()
        private_file(env["ca"], pem)
        private_file(env["trust"], json.dumps({"host": "127.0.0.1",
            "port": camera_inventory._INTEGRATION_PORT,
            "sha256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()}))
        with pytest.raises(InventoryError):
            await fetch_inventory("127.0.0.1", api_key_file=env["key"],
                                  trust_file=env["trust"], cert_file=env["ca"])
        assert env["seen"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["stale_version", "auth_failure", "redirect"])
async def test_stale_auth_and_redirect_never_fetch_cameras(tmp_path, monkeypatch, case):
    async with synthetic_protect(tmp_path, monkeypatch,
        meta_version="7.3.61" if case == "stale_version" else "7.3.60",
        meta_status=401 if case == "auth_failure" else 200,
        redirect=case == "redirect") as env:
        with pytest.raises(InventoryError):
            await fetch_inventory("127.0.0.1", api_key_file=env["key"],
                                  trust_file=env["trust"], cert_file=env["ca"])
        assert [path for path, _ in env["seen"]] == [
            "/proxy/protect/integration/v1/meta/info"]


def test_secret_files_must_be_private(tmp_path):
    path = private_file(tmp_path / "api-key", "synthetic")
    path.chmod(0o644)
    with pytest.raises(InventoryError):
        camera_inventory._read_private(path, 4096)
    path.chmod(0o600)
    link = tmp_path / "key-link"
    link.symlink_to(path)
    with pytest.raises(InventoryError):
        camera_inventory._read_private(link, 4096)


@pytest.mark.asyncio
async def test_controller_must_be_an_explicit_private_ipv4(tmp_path):
    for host in ("console.local", "8.8.8.8", "0.0.0.0", "169.254.1.1",
                 "192.168.0.1:443", "192.168.0.1/path"):
        with pytest.raises(InventoryError, match="private IPv4"):
            await fetch_inventory(host, api_key_file=tmp_path / "missing",
                                  trust_file=tmp_path / "missing", cert_file=tmp_path / "missing")

"""The public demo is a read-only preview, not an alternate administration site."""

from pathlib import Path
import socket

from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey import control_demo, control_site


async def test_preview_never_loads_state_or_connects_to_a_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo must not load configuration or contact a service")

    for name in ("ConfigurationStore", "AiPortConfigurationStore", "fetch_inventory",
                 "fetch_catalog", "probe_vision"):
        monkeypatch.setattr(control_site, name, forbidden)
    connect = socket.socket.connect

    def local_only(sock, address):
        assert address[0] in {"127.0.0.1", "::1"}
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    server = TestServer(control_demo.demo_app(), host="127.0.0.1")
    client = TestClient(server)
    await client.start_server()
    try:
        monkeypatch.setattr(Path, "read_text", forbidden)
        monkeypatch.setattr(Path, "read_bytes", forbidden)
        response = await client.get("/?config=/private/canary&provider=https://example.invalid/secret")
        page = await response.text()
        assert response.status == 200
        assert "Synthetic demo — not connected" in page
        assert "All results below are written examples" in page
        assert "Recognition quality, latency and provider cost</dt><dd>Not measured" in page
        assert "canary" not in page and "example.invalid" not in page
        assert "<form" not in page and "<script" not in page and "<img" not in page
        assert "form-action 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"
        assert "Set-Cookie" not in response.headers
        assert "Skip to demo content" in page and "<main id='main'>" in page
        assert page.count("aria-labelledby=") == 4
        assert "<nav aria-label='Demo sections'>" in page
    finally:
        await client.close()


@pytest.mark.parametrize("path", ["/", "/provider", "/provider/probe", "/rollout/apply",
                                 "/login", "/api/adopt", "/healthz"])
async def test_preview_has_no_mutation_or_device_routes(path):
    async with TestClient(TestServer(control_demo.demo_app(), host="127.0.0.1")) as client:
        response = await client.post(path, data={"csrf": "fictional", "url": "http://127.0.0.1:1"})
        assert response.status == (405 if path == "/" else 404)
        if path != "/":
            assert (await client.get(path)).status == 404


def test_cli_can_only_start_the_preview_on_loopback(monkeypatch):
    started = []
    monkeypatch.setattr(control_demo.web, "run_app", lambda app, **kwargs: started.append(kwargs))
    assert control_demo.main(["--port", "9876"]) == 0
    assert started[0]["host"] == "127.0.0.1" and started[0]["port"] == 9876
    assert started[0]["access_log"] is None


@pytest.mark.parametrize("args", [["--port", "0"], ["--port", "1023"], ["--port", "65536"],
                                 ["--host", "0.0.0.0"], ["--config", "/private/state.json"]])
def test_cli_refuses_host_exposure_and_state_inputs(args, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid arguments must not start a server")
    monkeypatch.setattr(control_demo.web, "run_app", forbidden)
    with pytest.raises(SystemExit) as stopped:
        control_demo.main(args)
    assert stopped.value.code == 2

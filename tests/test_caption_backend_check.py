"""No real model: exercise the public tool against disposable numeric loopback."""
import asyncio
import base64
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import stat

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image
import pytest

from aikey import provider_probe

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("caption_backend_check", ROOT / "tools/caption_backend_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def reply(text=None, **choice):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(check.EXPECTED) if text is None else text}, **choice}]}


def test_uses_worker_adapter_and_synthetic_images_without_giving_order_away():
    provider = check.make_provider("http://127.0.0.1:8000/v3", "fixture-model")
    payload = check.make_request(provider)
    assert payload["model"] == "fixture-model"
    assert payload["max_tokens"] == 128 and payload["temperature"] == 0
    assert payload["stream"] is False
    assert payload["response_format"]["json_schema"]["name"] == "session_description"
    assert payload["messages"][0]["role"] == "system"
    content = payload["messages"][1]["content"]
    assert [item["type"] for item in content] == ["image_url", "image_url", "text"]
    assert "red" not in content[-1]["text"] and "blue" not in content[-1]["text"]
    for item, rgb in zip(content, [(255, 0, 0), (0, 0, 255)]):
        raw = base64.b64decode(item["image_url"]["url"].split(",")[1])
        with Image.open(BytesIO(raw)) as image:
            assert image.size == (512, 512) and image.mode == "RGB"
            assert image.info == {} and image.getextrema() == tuple((v, v) for v in rgb)


@pytest.mark.parametrize("base", ["http://example.invalid/v1", "http://localhost/v1",
    "http://192.0.2.1/v1", "http://192.168.1.1/v3", "http://0.0.0.0/v1",
    "https://127.0.0.1/v1", "http://user:password@127.0.0.1/v1",
    "http://127.0.0.1/v1?key=fictional", "http://127.0.0.1/v1#secret",
    "http://[::ffff:192.168.1.1]/v1", "http://127.0.0.1:99999/v1"])
async def test_bad_destination_refuses_before_root_creation(tmp_path, base):
    root = tmp_path / "run"
    with pytest.raises(check.CheckError, match="explicit_http_loopback_required"):
        await check.run_check(base, "fixture", root, acknowledge_load=True)
    assert not root.exists()


@pytest.mark.parametrize("base", ["http://127.0.0.1:8000/v3", "http://[::1]:8000/v1"])
def test_numeric_loopback_supported(base):
    assert check.make_provider(base, "fixture").url.endswith("/chat/completions")


async def test_new_root_and_acknowledgement_before_contact(tmp_path):
    for root in [tmp_path / "fresh", tmp_path]:
        with pytest.raises(check.CheckError, match="explicit_load_acknowledgement_required"):
            await check.run_check("http://127.0.0.1:1/v3", "fixture", root)
    assert not (tmp_path / "fresh").exists()
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "absent")
    for root in [tmp_path, link]:
        with pytest.raises(check.CheckError, match="new_output_directory_required"):
            await check.run_check("http://127.0.0.1:1/v3", "fixture", root, acknowledge_load=True)


async def test_three_matches_are_identical_bounded_requests_and_private_receipts(tmp_path):
    requests = []
    async def endpoint(request):
        requests.append((await request.read(), dict(request.headers)))
        response = web.json_response(reply())
        response.set_cookie("fictional-cookie", "must-not-return")
        return response
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                       acknowledge_load=True)
    assert report["passed"] is True and report["started_requests"] == 3
    assert len(requests) == 3 and len({r[0] for r in requests}) == 1
    assert requests[0][0] == (root / "request.json").read_bytes()
    for _, headers in requests:
        assert "Cookie" not in headers and "Authorization" not in headers
        assert headers["Accept-Encoding"] == "identity"
    assert report["provider_charge_usd"] is None
    for name in ["cold_start_verified", "model_identity_verified", "native_verified", "quality_verified"]:
        assert report[name] is False
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for file in root.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    for row in report["results"]:
        assert row["status"] == "matched" and row["http_status"] == 200
        raw = (root / f"response-{row['number']}.bin").read_bytes()
        assert row["response_sha256"] == hashlib.sha256(raw).hexdigest()
        assert row["request_sha256"] == hashlib.sha256(requests[0][0]).hexdigest()
    assert json.loads((root / "report.json").read_bytes()) == report


@pytest.mark.parametrize("body,expected", [
    (reply('{"first_color":"black","second_color":"blue"}'), "answer_mismatch"),
    (reply('{"first_color":"blue","second_color":"red"}'), "answer_mismatch"),
    (reply('{"first_color":"red","second_color":"blue","extra":true}'), "answer_mismatch"),
    (reply('```json\n{"first_color":"red","second_color":"blue"}\n```'), "response_invalid"),
    (reply('{"first_color":"black","first_color":"red","second_color":"blue"}'), "response_invalid"),
    (reply('{"first_color":"red","second_color":"blue","extra":NaN}'), "response_invalid"),
    (reply(finish_reason="length"), "response_invalid"),
    (reply(message={"content": json.dumps(check.EXPECTED), "tool_calls": [{"id": "fixture"}]}), "response_invalid"),
    ({"error": "fictional diagnostic"}, "response_invalid"),
    ({"choices": [reply()["choices"][0]] * 2}, "response_invalid"),
])
async def test_raw_failure_is_saved_before_gate_and_never_retried(tmp_path, body, expected):
    calls = []
    raw = json.dumps(body).encode()
    async def endpoint(request):
        calls.append(await request.read())
        return web.Response(body=raw, content_type="application/json")
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                       acknowledge_load=True)
        with pytest.raises(check.CheckError, match="new_output_directory_required"):
            await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                  acknowledge_load=True)
    assert len(calls) == report["started_requests"] == 1
    assert report["passed"] is False and report["status"] == "failed"
    assert report["results"][0]["status"] == expected
    assert (root / "response-1.bin").read_bytes() == raw
    assert not (root / "admitted-2.json").exists()


@pytest.mark.parametrize("failure", [1, 2, 3])
async def test_any_failed_repetition_prevents_success(tmp_path, failure):
    calls = []
    async def endpoint(request):
        calls.append(await request.read())
        return web.json_response(reply('{"first_color":"black","second_color":"blue"}')
                                 if len(calls) == failure else reply())
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", tmp_path / "run",
                                       acknowledge_load=True)
    assert not report["passed"] and len(calls) == report["started_requests"] == failure


@pytest.mark.parametrize("kind", ["http", "redirect", "gzip", "oversized", "duplicate", "not-json"])
async def test_transport_and_outer_json_failures_never_retry_or_redirect(tmp_path, kind):
    calls = []
    async def endpoint(request):
        calls.append(request.path)
        await request.read()
        if kind == "http":
            return web.Response(status=503, body=b"fictional failure")
        if kind == "redirect":
            raise web.HTTPFound("/must-not-follow")
        if kind == "gzip":
            return web.Response(body=b"fictional encoded bytes", content_type="application/json",
                                headers={"Content-Encoding": "gzip"})
        if kind == "oversized":
            return web.Response(body=b" " * (check.MAX_REPLY_BYTES + 4000), content_type="application/json")
        if kind == "duplicate":
            raw = json.dumps(reply())[:-1] + ',"choices":[]}'
            return web.Response(text=raw, content_type="application/json")
        return web.Response(text="fictional failure", content_type="text/plain")
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                       acknowledge_load=True)
    assert not report["passed"] and calls == ["/v3/chat/completions"]
    assert len((root / "response-1.bin").read_bytes()) <= check.MAX_REPLY_BYTES + 1
    assert report["results"][0]["response_truncated"] is (kind == "oversized")


async def test_timeout_retains_partial_bytes_and_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "TIMEOUT_SECONDS", .05)
    async def endpoint(request):
        await request.read()
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(b'{"partial":')
        await asyncio.sleep(.1)
        return response
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                       acknowledge_load=True)
    assert not report["passed"] and report["results"][0]["status"] == "timeout"
    assert (root / "response-1.bin").read_bytes() == b'{"partial":'
    assert (root / "admitted-1.json").exists() and not (root / "admitted-2.json").exists()


async def test_cancellation_collects_client_and_preserves_uncertainty(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    async def endpoint(request):
        await request.read()
        entered.set()
        await release.wait()
        return web.json_response(reply())
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        task = asyncio.create_task(check.run_check(str(server.make_url("/v3")), "fixture", root,
                                                   acknowledge_load=True))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
    report = json.loads((root / "report.json").read_bytes())
    assert report["status"] == "cancelled" and not report["passed"]
    assert report["started_requests"] == 1 and report["results"][0]["status"] == "incomplete"
    assert not (root / "admitted-2.json").exists()


async def test_old_single_image_probe_can_match_while_structured_path_is_wrong(tmp_path, monkeypatch):
    # Reproduce the actual gap without a GPU: healthy single-image path is not
    # evidence for the structured multi-image path, even at the same endpoint.
    monkeypatch.setattr(provider_probe, "_challenge", lambda: (b"\x89PNG\r\n\x1a\nfixture", "A1B2C3D4"))
    async def endpoint(request):
        payload = await request.json()
        return web.json_response(reply('{"first_color":"black","second_color":"blue"}')
                                 if "response_format" in payload else reply("A1B2C3D4"))
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    async with TestServer(app) as server:
        base = str(server.make_url("/v3"))
        old = await provider_probe.probe_vision(check.make_provider(base, "fixture"))
        new = await check.run_check(base, "fixture", tmp_path / "run", acknowledge_load=True)
    assert old["synthetic_image_matched"] is True
    assert new["passed"] is False and new["results"][0]["status"] == "answer_mismatch"


async def test_raw_bytes_exist_before_matcher_runs(tmp_path, monkeypatch):
    root = tmp_path / "run"
    original = check.match_response
    checked = []
    def matcher(provider, raw):
        number = len(checked) + 1
        assert (root / f"response-{number}.bin").read_bytes() == raw
        checked.append(number)
        return original(provider, raw)
    monkeypatch.setattr(check, "match_response", matcher)
    async def endpoint(request):
        await request.read()
        return web.json_response(reply())
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    async with TestServer(app) as server:
        report = await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                       acknowledge_load=True)
    assert checked == [1, 2, 3] and report["passed"]


async def test_failed_admission_receipt_prevents_any_contact(tmp_path, monkeypatch):
    calls = []
    save = check._save
    def broken(root, name, raw):
        if name.startswith("admitted-"):
            raise OSError("fictional receipt failure")
        return save(root, name, raw)
    monkeypatch.setattr(check, "_save", broken)
    async def endpoint(request):
        calls.append(await request.read())
        return web.json_response(reply())
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        with pytest.raises(OSError):
            await check.run_check(str(server.make_url("/v3")), "fixture", root,
                                  acknowledge_load=True)
    assert not calls
    report = json.loads((root / "report.json").read_bytes())
    assert report["status"] == "incomplete" and not report["passed"]
    assert report["started_requests"] == 0


def test_cli_requires_explicit_acknowledgement_and_does_not_echo_endpoint(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        check.main(["--base-url", "http://127.0.0.1:1/v3", "--model", "fictional",
                    "--output", str(tmp_path / "run")])
    assert exit_info.value.code == 2 and not (tmp_path / "run").exists()
    output = capsys.readouterr()
    assert not output.out and "127.0.0.1" not in output.err


@pytest.mark.parametrize("matched", [True, False])
async def test_actual_cli_process_reports_exit_code_and_collects_requests(tmp_path, matched):
    import sys
    calls = []
    async def endpoint(request):
        calls.append(await request.read())
        return web.json_response(reply() if matched else reply("fictional private server response"))
    app = web.Application()
    app.router.add_post("/v3/chat/completions", endpoint)
    root = tmp_path / "run"
    async with TestServer(app) as server:
        process = await asyncio.create_subprocess_exec(sys.executable,
            str(ROOT / "tools/caption_backend_check.py"), "--base-url", str(server.make_url("/v3")),
            "--model", "fixture", "--output", str(root), "--acknowledge-load",
            env={}, cwd=tmp_path, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert process.returncode == (0 if matched else 1)
    assert not stderr and b"fictional private" not in stdout
    summary = json.loads(stdout)
    assert summary["passed"] is matched and len(calls) == (3 if matched else 1)
    assert json.loads((root / "report.json").read_bytes())["passed"] is matched

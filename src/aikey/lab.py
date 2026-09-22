"""Independent synthetic controller and inference servers, bound only to loopback.

This exercises real TLS, HTTP and WebSockets. It never contacts a real console,
loads model weights or treats a synthetic result as a real camera observation.
"""

import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import ssl
import struct
import tempfile
import time

import aiohttp
from aiohttp import web

from .config import hydrate_secrets, initialize
from .runtime import Application
from .tls import ensure_identity_certificate, server_context


def _wire(header, body):
    # Independent fixture assembler, separate from the production codec.
    chunks = []
    for kind, value in ((1, header), (2, body)):
        raw = json.dumps(value, separators=(",", ":")).encode()
        chunks.append(bytes([kind, 1, 0, 0]) + len(raw).to_bytes(4, "big") + raw)
    return b"".join(chunks)


def _read(raw):
    records, offset = [], 0
    for kind in (1, 2):
        record_type, fmt, compressed, reserved, length = struct.unpack_from(">BBBBI", raw, offset)
        if (record_type, fmt, compressed, reserved) != (kind, 1, 0, 0):
            raise AssertionError("Unexpected fixture response framing")
        offset += 8
        records.append(json.loads(raw[offset:offset + length]))
        offset += length
    if offset != len(raw):
        raise AssertionError("Trailing fixture response data")
    return records


async def _serve(app, *, context=None):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


async def run_lab() -> dict:
    checks = []
    counters = {"inference": 0, "callbacks": 0, "media": 0, "connections": 0}
    callbacks, prefixes = [], []
    response_futures = {}
    control_connections, search_connections = asyncio.Queue(), asyncio.Queue()
    enrolled = set()
    token = secrets.token_urlsafe(24)
    application = None
    controller_runner = model_runner = None
    description = "SYNTHETIC LAB FIXTURE. No camera image was analyzed by a model."
    with tempfile.TemporaryDirectory(prefix="local-aikey-lab-") as temporary:
        root = Path(temporary)
        config = initialize(root / "config.json", root / "device")
        ensure_identity_certificate(root / "controller", "020000000001")
        controller_context = server_context(root / "controller")
        controller_context.verify_mode = ssl.CERT_REQUIRED
        controller_context.load_verify_locations(root / "device/device.crt")
        expected_peer = ssl.PEM_cert_to_DER_cert((root / "device/device.crt").read_text())

        def verify_device(request):
            ssl_object = request.transport.get_extra_info("ssl_object")
            if not ssl_object or ssl_object.getpeercert(binary_form=True) != expected_peer:
                raise web.HTTPForbidden()
            if request.headers.get("x-ident", "").upper() != config["device"]["mac"]:
                raise web.HTTPForbidden()

        async def socket(request):
            verify_device(request)
            ident = request.headers["x-ident"].upper()
            if ident not in enrolled:
                if request.headers.get("x-token") != token:
                    raise web.HTTPForbidden()
                enrolled.add(ident)
            elif request.headers.get("x-token"):
                raise web.HTTPForbidden(text="Consumed token was unexpectedly replayed")
            ws = web.WebSocketResponse(protocols=("ucp4",))
            await ws.prepare(request)
            counters["connections"] += 1
            await control_connections.put(ws)
            async for incoming in ws:
                if incoming.type != aiohttp.WSMsgType.BINARY:
                    continue
                header, body = _read(incoming.data)
                if header.get("type") == "response":
                    future = response_futures.pop(header["id"], None)
                    if future and not future.done():
                        future.set_result((header, body))
                elif header.get("action") == "timeSync":
                    now = int(time.time() * 1000)
                    await ws.send_bytes(_wire({"type": "response", "id": header["id"],
                        "timestamp": now, "error": None, "errorCode": 0},
                        {"t0": body["t0"], "t1": now, "t2": now}))
            return ws

        async def query_socket(request):
            verify_device(request)
            if request.headers["x-ident"].upper() not in enrolled:
                raise web.HTTPForbidden()
            ws = web.WebSocketResponse(protocols=("ucp4",))
            await ws.prepare(request)
            await search_connections.put(ws)
            async for incoming in ws:
                if incoming.type != aiohttp.WSMsgType.BINARY:
                    continue
                header, body = _read(incoming.data)
                if header.get("type") == "response":
                    future = response_futures.pop(header["id"], None)
                    if future and not future.done():
                        future.set_result((header, body))
                elif header.get("action") == "echo":
                    await ws.send_bytes(_wire({"type": "response", "id": header["id"],
                        "timestamp": int(time.time() * 1000), "error": None, "errorCode": 0}, body))
            return ws

        async def media(request):
            verify_device(request)
            counters["media"] += 1
            image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV7sAAAAASUVORK5CYII=")
            return web.Response(body=image, content_type="image/png")

        async def callback(request):
            verify_device(request)
            payload = await request.json()
            callbacks.append(payload)
            counters["callbacks"] += 1
            return web.json_response({"received": True})

        async def infer(request):
            assert not request.headers.get("x-ident")
            payload = await request.json()
            assert payload["model"] == "synthetic-lab-vision"
            assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/")
            counters["inference"] += 1
            return web.json_response({"choices": [{"finish_reason": "stop", "message": {"content": description}}]})

        async def embed(request):
            payload = await request.json()
            assert not request.headers.get("x-ident")
            prefixes.extend(payload["input"])
            return web.json_response({"data": [{"index": index,
                "embedding": [1.0] + [0.0] * 383} for index, _ in enumerate(payload["input"])],
                "model": payload["model"]})

        async def command(ws, action, body):
            request_id = secrets.token_hex(8)
            future = asyncio.get_running_loop().create_future()
            response_futures[request_id] = future
            await ws.send_bytes(_wire({"type": "request", "id": request_id,
                "timestamp": int(time.time() * 1000), "action": action}, body))
            return await asyncio.wait_for(future, timeout=10)

        try:
            controller_app = web.Application()
            controller_app.router.add_get("/", socket)
            controller_app.router.add_get("/wss/nl-search/v1", query_socket)
            controller_app.router.add_get("/internal/aiprocessors/image/fixture", media)
            controller_app.router.add_post("/internal/aiprocessors/descriptions/fixture-task", callback)
            controller_runner, controller_port = await _serve(controller_app, context=controller_context)
            model_app = web.Application()
            model_app.router.add_post("/v1/chat/completions", infer)
            model_app.router.add_post("/v1/embeddings", embed)
            model_runner, model_port = await _serve(model_app)
            config["runtime"].update(mode="lab", https_port=0)
            config["controller"].update(host="127.0.0.1", control_port=controller_port,
                search_port=controller_port, media_port=controller_port,
                ca_file=str(root / "controller/device.crt"), verify_hostname=True)
            config["controller_origins"] = [f"https://127.0.0.1:{controller_port}"]
            config["controller_media_origin"] = config["controller_origins"][0]
            config["inference"].update(base_url=f"http://127.0.0.1:{model_port}/v1",
                                       model="synthetic-lab-vision")
            config["embeddings"].update(base_url=f"http://127.0.0.1:{model_port}/v1")
            config["worker"]["description_embeddings"] = True
            config["search"].update(enabled=True, reconnect_seconds=1)
            hydrated = hydrate_secrets(config)
            application = Application(hydrated)
            await application.start()
            manager_tls = ssl.create_default_context(cafile=root / "device/device.crt")
            manager_tls.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=manager_tls)) as manager:
                management_url = f"https://127.0.0.1:{application.https_port}"
                adoption = {"username": hydrated["device"]["management_username"],
                    "password": "incorrect-fixture-password", "token": token, "protocol": "wss",
                    "mode": 0, "hosts": [f"127.0.0.1:{controller_port}"]}
                async with manager.post(management_url + "/api/adopt", json=adoption) as response:
                    assert response.status == 401
                checks.append("management_rejects_wrong_password")
                adoption["password"] = hydrated["device"]["management_password"]
                async with manager.post(management_url + "/api/adopt", json=adoption) as response:
                    assert response.status == 200, await response.text()
                    assert token not in await response.text()
                ws = await asyncio.wait_for(control_connections.get(), timeout=10)
                checks.append("normal_token_adoption_over_tls")
                info_header, info = await command(ws, "getInfo", {})
                assert info_header["errorCode"] == 0
                assert info["mac"].replace(":", "").upper() == config["device"]["mac"]
                assert info["featureFlags"]["supportFaceRecognition"]["enabled"] is False
                checks.append("device_info_and_honest_capabilities")
                unknown, _ = await command(ws, "executeShell", {"command": "synthetic-noop"})
                assert unknown["errorCode"] != 0
                checks.append("unsupported_command_rejected")
                job = {"targetUri": ":7968/describe", "timeoutMs": 15000,
                    "resUrl": config["controller_origins"][0] + "/internal/aiprocessors/descriptions/fixture-task",
                    "payload": {"camera": "fixture-camera", "event": "fixture-event", "pass": "open",
                        "promptProfile": "session-v1", "images": [
                            {"reqUrl": "/internal/aiprocessors/image/fixture"}]}}
                acknowledgment, echo = await command(ws, "RequestAI", job)
                assert acknowledgment["errorCode"] == 0 and echo == job
                await asyncio.wait_for(application.worker.wait_for_idle(), timeout=15)
                assert counters["callbacks"] == 1, application.worker.status()
                assert callbacks[0]["description"] == description
                assert callbacks[0]["camera"] == "fixture-camera"
                assert callbacks[0]["event"] == "fixture-event"
                assert len(callbacks[0]["descEmbedding"]) == 384
                checks.extend(["mtls_media_download", "vision_http_request", "task_callback_preserves_identity",
                               "description_embedding_384"])
                query_ws = await asyncio.wait_for(search_connections.get(), timeout=10)
                query_header, query_result = await command(query_ws, "NL_PARSE",
                    {"model": "multilingual-e5-small", "querySentence": "synthetic fixture"})
                assert query_header["errorCode"] == 0
                assert len(query_result["txtEmbed"]) == 384
                assert any(value.startswith("passage: ") for value in prefixes)
                assert any(value.startswith("query: ") for value in prefixes)
                checks.append("e5_query_socket_and_document_query_prefixes")
                assert token not in (root / "device/device-state.json").read_text()
                await application.stop()
                application = Application(hydrated)
                await application.start()
                ws = await asyncio.wait_for(control_connections.get(), timeout=10)
                replay, _ = await command(ws, "RequestAI", job)
                assert replay["errorCode"] == 0
                await application.worker.wait_for_idle()
                assert counters["callbacks"] == 1 and counters["inference"] == 1
                checks.extend(["restart_reuses_identity_without_adoption_token", "persisted_job_deduplication"])
                health_url = f"https://127.0.0.1:{application.https_port}/healthz"
                async with manager.get(health_url) as response:
                    health = await response.json()
                    assert health["device"]["adopted"] is True
                    assert health["native_compatibility"] == "needs_evidence"
                    encoded_health = json.dumps(health)
                    assert token not in encoded_health
                    assert hydrated["device"]["management_password"] not in encoded_health
                checks.append("health_status_contains_no_credentials")
            return {"passed": True, "scope": "synthetic loopback integration", "checks": checks,
                "counts": counters, "timestamp": datetime.now(timezone.utc).isoformat(),
                "real_controller_contacted": False, "real_inference_performed": False,
                "native_protect_7_3_56": "needs_evidence", "nas_deployment": "not_run"}
        finally:
            if application:
                await application.stop()
            if controller_runner:
                await controller_runner.cleanup()
            if model_runner:
                await model_runner.cleanup()

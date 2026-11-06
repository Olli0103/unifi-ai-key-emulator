"""Control lifetimes must not reuse a previous audio permission grant."""

import asyncio
import json
import ssl
import threading
import time
from unittest.mock import AsyncMock

import pytest

from test_aiport_speech import (
    CAMERA, OTHER, Clock, audio_settings, feed, noise, service_for, voice,
)


@pytest.mark.parametrize("boundary", ["stream_stop", "reset", "new_control"])
async def test_audio_requires_fresh_settings_after_stream_lifetime(tmp_path, boundary):
    service, sink = await service_for(tmp_path, speech=(CAMERA, OTHER))
    clock = Clock()
    try:
        await audio_settings(service, sink, 1, enableAlrmBark=1)
        await audio_settings(service, sink, 2, deviceID=OTHER)
        await feed(service, CAMERA, noise(1) + voice(2), clock)
        assert service._audio_is_open(CAMERA, "alrmSpeak")
        if boundary == "stream_stop":
            service.ingress.control = AsyncMock(return_value={"status": "stopped"})
            await service._handle_diagnostic_frame(sink, json.dumps({
                "functionName": "UiStreamControl", "messageId": 3,
                "payload": {"deviceID": CAMERA, "streaming": False},
            }).encode())
        elif boundary == "reset":
            await service._handle_diagnostic_frame(sink, json.dumps({
                "functionName": "ResetAIPortStreams", "messageId": 3, "payload": {},
            }).encode())
        else:
            await service._send_diagnostic_hello(sink)
            service._hello_agreed = True
            await service._handle_diagnostic_frame(sink, json.dumps({
                "functionName": "ubnt_avclient_paramAgreement", "messageId": 3,
            }).encode())
        assert not service._audio_is_open(CAMERA, "alrmSpeak")
        before = len(sink.events("EventSmartAudio"))
        await feed(service, CAMERA, noise(1) + voice(2) + noise(4), clock)
        assert len(sink.events("EventSmartAudio")) == before
        assert not service._speech_enabled.get(CAMERA)
        assert CAMERA not in service._speech_settings_seen
        assert not service._audio_flags.get(CAMERA)
        if boundary == "stream_stop":
            assert service._speech_enabled[OTHER]
        await audio_settings(service, sink, 4)
        await feed(service, CAMERA, noise(1) + voice(2) + noise(4), clock)
        assert [x["alrmSpeak"] for x in sink.events("EventSmartAudio")[before:]] == [
            "enter", "leave",
        ]
    finally:
        await service.stop()


async def test_real_tls_control_disconnect_revokes_without_closing_grace_streams(tmp_path):
    import aiohttp
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from test_aiport_speech import settings

    service, _ = await service_for(tmp_path)
    service.config["controller_ip"] = "127.0.0.1"
    service.config["diagnostic_hello_until"] = int(time.time()) + 60
    disconnected = asyncio.Event()
    original_schedule = service._schedule_ingress_close

    async def schedule():
        await original_schedule()
        disconnected.set()

    service._schedule_ingress_close = schedule

    async def websocket(request):
        ws = web.WebSocketResponse(protocols=["secure_transfer"])
        await ws.prepare(request)
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.BINARY:
                continue
            message = json.loads(frame.data)
            name = message["functionName"]
            if name == "ubnt_avclient_hello":
                await ws.send_bytes(json.dumps({"functionName": name,
                    "inResponseTo": message["messageId"]}).encode())
                await ws.send_bytes(json.dumps({"functionName":
                    "ubnt_avclient_paramAgreement", "messageId": 20}).encode())
            elif name == "ubnt_avclient_paramAgreement":
                await ws.send_bytes(json.dumps({"functionName": "ChangeAudioEventsSettings",
                    "messageId": 21, "payload": settings(enableAlrmBark=1)}).encode())
            elif message.get("inResponseTo") == 21:
                assert message["statusCode"] == 0
                assert service._speech_enabled[CAMERA]
                await ws.close(code=1000)
        return ws

    app = web.Application()
    app.router.add_get("/camera/1.0/ws", websocket)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "device.crt", tmp_path / "device.key")
    server = TestServer(app)
    await server.start_server(ssl=context)
    service.control_port = server.port
    service.task = asyncio.create_task(service._connect_loop())
    try:
        await asyncio.wait_for(disconnected.wait(), 3)
        assert not service._speech_enabled and not service._audio_flags
        assert not service._speech_settings_seen and not service._params_agreed
        assert service._current_ws is None
        assert service._ingress_close_task is not None  # existing decoder grace retained
        assert service.ingress.list_streams() == [{"deviceID": CAMERA}, {"deviceID": OTHER}]
    finally:
        await service.stop()
        await server.close()


async def test_failed_status_send_still_revokes_only_affected_camera(tmp_path):
    service, sink = await service_for(tmp_path, speech=(CAMERA, OTHER))
    try:
        await audio_settings(service, sink, 1)
        await audio_settings(service, sink, 2, deviceID=OTHER)
        service._speech_reannounces[CAMERA] = 3
        service._speech_enter_times[CAMERA] = [123.0]
        sink.send_bytes = AsyncMock(side_effect=ConnectionError("synthetic socket failure"))
        with pytest.raises(ConnectionError):
            await service._send_stream_status(sink, streaming=False, camera_mac=CAMERA)
        assert not service._speech_enabled.get(CAMERA)
        assert CAMERA not in service._speech_settings_seen
        assert CAMERA not in service._speech_reannounces
        assert service._speech_enter_times[CAMERA] == [123.0]  # no rate-budget reset
        assert service._speech_enabled[OTHER]
    finally:
        await service.stop()


async def test_enter_already_writing_is_closed_by_concurrent_stream_stop(tmp_path):
    service, sink = await service_for(tmp_path)
    writing, release = asyncio.Event(), asyncio.Event()
    original_send = sink.send_bytes

    async def send(data):
        message = json.loads(data)
        if (message["functionName"] == "EventSmartAudio"
                and message["payload"]["alrmSpeak"] == "enter"):
            writing.set()
            await release.wait()
        await original_send(data)

    sink.send_bytes = send
    tasks = []
    try:
        await audio_settings(service, sink, 1)
        tasks.append(asyncio.create_task(service._send_audio_edge(
            sink, CAMERA, "alrmSpeak", "enter", -20.0,
            generation=service._audio_generation.get(CAMERA, 0))))
        await asyncio.wait_for(writing.wait(), 2)
        tasks.append(asyncio.create_task(service._send_stream_status(
            sink, streaming=False, camera_mac=CAMERA)))
        await asyncio.sleep(0)  # let stop revoke before the in-flight send finishes
        assert not service._speech_enabled.get(CAMERA)
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert [x["alrmSpeak"] for x in sink.events("EventSmartAudio")] == ["enter", "leave"]
        assert not service._audio_open
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.stop()


async def test_invalid_reset_does_not_revoke_existing_audio_grant(tmp_path):
    service, sink = await service_for(tmp_path)
    try:
        await audio_settings(service, sink, 1)
        await service._handle_diagnostic_frame(sink, json.dumps({
            "functionName": "ResetAIPortStreams", "messageId": 2,
            "payload": {"unexpected": True},
        }).encode())
        assert sink.replies("ResetAIPortStreams")[-1]["statusCode"] != 0
        assert service._speech_enabled[CAMERA]
        assert CAMERA in service._speech_settings_seen
    finally:
        await service.stop()


@pytest.mark.parametrize("cancel_reader", [False, True])
async def test_inflight_sound_cannot_cross_stop_and_new_grant(tmp_path, cancel_reader):
    from aikey.aiport_sounds import SoundEdge
    from test_aiport_sounds import Scores, sound_service
    from test_aiport_speech import pcm

    service, sink = await sound_service(tmp_path, Scores(), Clock())
    started, release = threading.Event(), threading.Event()
    sounds = service._sounds[CAMERA]
    resets = []
    original_reset = sounds.reset

    def reset():
        assert release.is_set(), "reset raced a running classifier"
        resets.append(True)
        original_reset()

    def blocked_feed(data):
        started.set()
        assert release.wait(5), "test failed to release classifier"
        return [SoundEdge("alrmBark", "enter", -20.0)]

    sounds.feed, sounds.reset = blocked_feed, reset
    pending = None
    try:
        await audio_settings(service, sink, 1, enableAlrmSpeak=0, enableAlrmBark=1)
        pending = asyncio.create_task(service._observe_pool_audio(CAMERA, pcm(noise(0.3))))
        assert await asyncio.to_thread(started.wait, 2)
        if cancel_reader:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await service._send_stream_status(sink, streaming=False, camera_mac=CAMERA)
        await audio_settings(service, sink, 2, enableAlrmSpeak=0, enableAlrmBark=1)
        assert not resets
        if cancel_reader:
            # A new decoder cannot concurrently feed the old sound detector.
            await service._observe_pool_audio(CAMERA, pcm(noise(0.3)))
        release.set()
        if not cancel_reader:
            await asyncio.wait_for(pending, 2)
        else:
            await asyncio.wait_for(asyncio.shield(service._sound_work[CAMERA]), 2)
        assert resets == [True]
        assert sink.events("EventSmartAudio") == []
        assert not service._audio_open
        # Fresh audio under the new grant is usable, not permanently blocked.
        await service._observe_pool_audio(CAMERA, pcm(noise(0.3)))
        assert [e["alrmBark"] for e in sink.events("EventSmartAudio")] == ["enter"]
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.stop()

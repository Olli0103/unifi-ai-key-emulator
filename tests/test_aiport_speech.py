"""Speech presence on AI Port relay audio (#15, #28). Synthetic PCM only."""

import array
import json
import math
import random
import sys

import pytest

from aikey.aiport_audio import (AUDIO_TYPES, FRAME_BYTES, SAMPLE_RATE, AudioSettingsError,
                                SpeechActivity, parse_audio_settings, speech_event_payload)
from aikey.aiport_candidate import CandidateError, load_config
from test_aiport_candidate import fixture_state, private_file

CAMERA = "2A1122334455"
OTHER = "2A1122334466"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def pcm(samples):
    return array.array("h", (max(-32768, min(32767, int(v))) for v in samples)).tobytes()


def voice(seconds, amplitude=6000, seed=1):
    """A voiced, syllable-modulated harmonic signal (like speech, not a recording)."""
    rng = random.Random(seed)
    out = []
    for n in range(int(seconds * SAMPLE_RATE)):
        t = n / SAMPLE_RATE
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 4 * t)
        tone = sum(math.sin(2 * math.pi * f * t) / k for k, f in enumerate((180, 360, 540, 900), 1))
        out.append(amplitude * envelope * tone / 2 + rng.gauss(0, 30))
    return out


def noise(seconds, amplitude=30, seed=2):
    rng = random.Random(seed)
    return [rng.gauss(0, amplitude) for _ in range(int(seconds * SAMPLE_RATE))]


def hum(seconds, amplitude=8000):
    return [amplitude * math.sin(2 * math.pi * 50 * n / SAMPLE_RATE)
            for n in range(int(seconds * SAMPLE_RATE))]


def run(detector, clock, samples, *, chunk=FRAME_BYTES * 10):
    data, edges = pcm(samples), []
    for offset in range(0, len(data), chunk):
        piece = data[offset:offset + chunk]
        clock.now += len(piece) / 2 / SAMPLE_RATE
        edges += [edge.edge for edge in detector.feed(piece)]
    return edges


def test_speech_after_quiet_enters_and_silence_leaves():
    clock = Clock()
    detector = SpeechActivity(clock=clock)
    assert run(detector, clock, noise(2)) == []
    assert run(detector, clock, voice(2)) == ["enter"]
    assert run(detector, clock, noise(4)) == ["leave"]
    assert detector.active is False


def test_steady_hum_and_hiss_and_near_silence_never_enter():
    for samples in (hum(4), noise(4, amplitude=9000), noise(4, amplitude=3)):
        clock = Clock()
        detector = SpeechActivity(clock=clock)
        run(detector, clock, noise(1))
        assert run(detector, clock, samples) == [], samples[:1]


def test_a_brief_click_is_too_short_to_enter():
    clock = Clock()
    detector = SpeechActivity(clock=clock)
    run(detector, clock, noise(2))
    assert run(detector, clock, voice(0.3) + noise(3)) == []


def test_a_long_passage_is_capped_and_can_reenter():
    clock = Clock()
    detector = SpeechActivity(clock=clock)
    run(detector, clock, noise(1))
    edges = run(detector, clock, voice(125, seed=3))
    assert edges[:2] == ["enter", "leave"] and edges.count("enter") >= 1


def test_the_payload_carries_every_audio_type_and_the_required_clocks():
    payload = speech_event_payload("2a:11:22:33:44:55", "enter", clock_wall_ms=1_700_000_000_000,
                                   level_db=-23.456)
    assert payload["deviceID"] == CAMERA
    assert payload["alrmSpeak"] == "enter"
    assert all(payload[kind] == "none" for kind in AUDIO_TYPES if kind != "alrmSpeak")
    for key in ("clockMonotonic", "clockStream", "clockStreamRate", "clockWall", "eventId",
                "leveldB", "levels"):
        assert isinstance(payload[key], (int, float))
    assert payload["loudNoise"] == payload["soundLoss"] == "none"
    assert payload["leveldB"] == -23.5
    leave = speech_event_payload(CAMERA, "leave", clock_wall_ms=1, level_db=-200)
    assert leave["alrmSpeak"] == "leave" and leave["leveldB"] == -120.0
    for bad in ({"edge": "moving"}, {"clock_wall_ms": 0}, {"level_db": float("nan")}):
        args = {"edge": "enter", "clock_wall_ms": 1, "level_db": -20.0} | bad
        with pytest.raises(AudioSettingsError):
            speech_event_payload(CAMERA, args.pop("edge"), **args)


def settings(**overrides):
    payload = {"deviceID": CAMERA, "enableAlrmSmoke": 0, "enableAlrmCmonx": 0,
               "enableAlrmSiren": 0, "enableAlrmSpeak": 1, "enableAlrmBabyCry": 0,
               "enableAlrmBark": 0, "enableAlrmBurglar": 0, "enableAlrmCarHorn": 0,
               "enableAlrmGlassBreak": 0, "recordEventSpeak": 0, "recordEventSmoke": 0,
               "recordEventCmonx": 0, "sendPulse": 0}
    payload.update(overrides)
    return payload


def test_protect_audio_settings_are_parsed_strictly():
    assert parse_audio_settings(settings()) == (CAMERA, True)
    assert parse_audio_settings(settings(enableAlrmSpeak=0)) == (CAMERA, False)
    bad = [settings(enableAlrmSpeak=2), settings(enableAlrmSpeak=True), settings(deviceID="x"),
           {k: v for k, v in settings().items() if k != "enableAlrmSpeak"}, [], None,
           settings(enableAlrmBark="1")]
    for payload in bad:
        with pytest.raises(AudioSettingsError):
            parse_audio_settings(payload)


def pool_config(tmp_path, *, speech=(CAMERA,)):
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{"camera_mac": mac, "source_ip": "192.168.10.1",
                                 "ffmpeg_path": sys.executable} for mac in (CAMERA, OTHER)]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8, "smart_types": ["person"],
        "max_events_per_hour": 12, "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1", "allow_remote": True,
                            "max_output_tokens": 256, "api_key_file": str(tmp_path / "api-key")}}
    if speech is not None:
        config["live_speech_cameras"] = list(speech)
    return config


def test_speech_cameras_must_be_paired_pool_members(tmp_path):
    for speech in ([CAMERA], ["2a:11:22:33:44:55", OTHER]):
        path = tmp_path / "ok.json"
        private_file(path, json.dumps(pool_config(tmp_path, speech=speech)).encode())
        assert load_config(path, check_decoder_executable=False)["live_speech_cameras"][0] == CAMERA
    for speech in ([], ["2A99887766AA"], [CAMERA, CAMERA], "x"):
        path = tmp_path / "bad.json"
        private_file(path, json.dumps(pool_config(tmp_path, speech=speech)).encode())
        with pytest.raises(CandidateError):
            load_config(path, check_decoder_executable=False)
    config = pool_config(tmp_path)
    del config["live_pool_detector"]
    path = tmp_path / "nodetector.json"
    private_file(path, json.dumps(config).encode())
    with pytest.raises(CandidateError):
        load_config(path, check_decoder_executable=False)


class Sink:
    def __init__(self):
        self.messages = []

    async def send_bytes(self, raw):
        self.messages.append(json.loads(raw))

    def events(self, name):
        return [m["payload"] for m in self.messages if m["functionName"] == name
                and m.get("inResponseTo") == 0]

    def replies(self, name):
        return [m for m in self.messages if m["functionName"] == name and m.get("inResponseTo")]


async def health(service):
    return json.loads((await service._health(None)).text)


async def service_for(tmp_path, **kwargs):
    from aikey.aiport_candidate import CandidateService
    service = CandidateService(pool_config(tmp_path, **kwargs), tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": CAMERA}, {"deviceID": OTHER}]
    service.ingress.audio_ready = lambda camera: True
    sink = Sink()
    service._current_ws = sink
    return service, sink


async def audio_settings(service, sink, message_id, **overrides):
    command = {"functionName": "ChangeAudioEventsSettings", "messageId": message_id,
               "payload": settings(**overrides)}
    await service._handle_diagnostic_frame(sink, json.dumps(command).encode())


async def feed(service, camera, samples, clock):
    service._speech[camera]._clock = clock
    data = pcm(samples)
    for offset in range(0, len(data), 9600):
        clock.now += 0.3
        await service._observe_pool_audio(camera, data[offset:offset + 9600])


async def test_speech_is_announced_then_sent_only_when_protect_enables_it(tmp_path):
    service, sink = await service_for(tmp_path)
    clock = Clock()
    try:
        await feed(service, CAMERA, noise(1) + voice(2) + noise(4), clock)
        status = sink.events("EventAIPortStatus")
        assert status and status[0]["isAudioEventReady"] is True
        flags = sink.events("EventFeatureFlagsUpdated")
        assert flags and "alrmSpeak" in flags[0]["smartDetect"]
        assert sink.events("EventSmartAudio") == []            # not enabled yet
        assert service.speech_edges_suppressed == 1

        await audio_settings(service, sink, 21)
        assert sink.replies("ChangeAudioEventsSettings")[-1]["statusCode"] == 0
        await feed(service, CAMERA, voice(2, seed=5) + noise(4), clock)
        edges = [(e["deviceID"], e["alrmSpeak"]) for e in sink.events("EventSmartAudio")]
        assert edges == [(CAMERA, "enter"), (CAMERA, "leave")]
        health_speech = (await health(service))["speech"]
        assert health_speech["events_entered"] == health_speech["events_left"] == 1
        assert health_speech["open"] == 0 and health_speech["enabled"] == 1
    finally:
        await service.stop()


async def test_disabling_speech_closes_an_open_event_and_other_cameras_are_refused(tmp_path):
    service, sink = await service_for(tmp_path)
    clock = Clock()
    try:
        await audio_settings(service, sink, 30)
        await feed(service, CAMERA, noise(1) + voice(2), clock)
        assert [e["alrmSpeak"] for e in sink.events("EventSmartAudio")] == ["enter"]
        await audio_settings(service, sink, 31, enableAlrmSpeak=0)
        assert [e["alrmSpeak"] for e in sink.events("EventSmartAudio")] == ["enter", "leave"]
        await audio_settings(service, sink, 32, deviceID=OTHER)
        assert sink.replies("ChangeAudioEventsSettings")[-1]["statusCode"] == 501
        await audio_settings(service, sink, 33, enableAlrmSpeak=7)
        assert sink.replies("ChangeAudioEventsSettings")[-1]["statusCode"] == 501
        assert OTHER not in service._speech                     # no audio decoder for it
        assert (await health(service))["speech"]["settings_rejected"] == 2
    finally:
        await service.stop()


async def test_without_speech_cameras_nothing_changes(tmp_path):
    service, sink = await service_for(tmp_path, speech=None)
    try:
        assert "speech" not in await health(service)
        await audio_settings(service, sink, 40)
        assert sink.replies("ChangeAudioEventsSettings")[-1]["statusCode"] == 501
        await service._send_stream_status(sink, streaming=True, camera_mac=CAMERA)
        assert sink.events("EventAIPortStatus")[-1]["isAudioEventReady"] is False
        assert all("alrmSpeak" not in f["smartDetect"] for f in sink.events("EventFeatureFlagsUpdated"))
    finally:
        await service.stop()


def fake_decoder(tmp_path, body):
    script = tmp_path / "fake-ffmpeg"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o700)
    return str(script)


async def test_the_audio_decoder_feeds_pcm_and_backs_off_when_there_is_no_audio(tmp_path):
    import asyncio
    from aikey.aiport_ingest import AiPortIngress, StreamSpec

    received = []

    async def observer(chunk):
        received.append(len(chunk))

    spec = StreamSpec(CAMERA, "192.168.10.1", "alias", 1280, 720, 15.0, 2)
    flowing = fake_decoder(tmp_path, "head -c 28800 /dev/zero; sleep 5")
    ingress = AiPortIngress(camera_mac=CAMERA, source_ip="192.168.10.1",
                            ffmpeg_path=flowing, audio_observer=observer)
    try:
        await ingress._restart_audio_locked(spec)
        for _ in range(50):
            if len(received) == 3:
                break
            await asyncio.sleep(0.05)
        assert received == [9600, 9600, 9600]
        assert ingress.audio_ready and ingress.audio_starts == 1
    finally:
        await ingress._close_locked()
    assert not ingress.audio_ready

    silent = AiPortIngress(camera_mac=CAMERA, source_ip="192.168.10.1",
                           ffmpeg_path=fake_decoder(tmp_path, "exit 1"), audio_observer=observer)
    try:
        await silent._restart_audio_locked(spec)
        await asyncio.sleep(0.2)
        await silent._restart_audio_locked(spec)          # the first produced nothing
        assert silent.audio_failures == 1 and silent._audio_delay == 10.0
        assert not silent.audio_ready
    finally:
        await silent._close_locked()


async def test_a_camera_without_settings_gets_a_bounded_readiness_pulse(tmp_path, monkeypatch):
    import aikey.aiport_candidate as candidate
    service, sink = await service_for(tmp_path)
    now = [500.0]
    monkeypatch.setattr(candidate.time, "monotonic", lambda: now[0])
    quiet = pcm(noise(0.3))
    try:
        await service._observe_pool_audio(CAMERA, quiet)          # announce
        assert [s["isAudioEventReady"] for s in sink.events("EventAIPortStatus")] == [True]
        now[0] += 30
        await service._observe_pool_audio(CAMERA, quiet)          # too early
        for _ in range(5):
            now[0] += 61
            await service._observe_pool_audio(CAMERA, quiet)
        ready = [s["isAudioEventReady"] for s in sink.events("EventAIPortStatus")]
        assert ready == [True, False, True, False, True, False, True]   # three pulses only
        assert (await health(service))["speech"]["reannounces"] == 3

        await audio_settings(service, sink, 50, deviceID=OTHER)            # refused camera
        await service._observe_pool_audio(OTHER, quiet)                    # not a speech camera
        await audio_settings(service, sink, 51)                            # settings arrive
        now[0] += 120
        before = len(sink.events("EventAIPortStatus"))
        service._speech_reannounces.clear()
        await service._observe_pool_audio(CAMERA, quiet)
        assert len(sink.events("EventAIPortStatus")) == before             # no pulse once seen
        camera_health = service._speech_camera_health(CAMERA)["speech"]
        assert camera_health == {"enabled": True, "events": 0, "open": False}
        assert service._speech_camera_health(OTHER) == {}
    finally:
        await service.stop()


async def test_a_disabled_camera_is_pulsed_every_ten_minutes_until_enabled(tmp_path, monkeypatch):
    import aikey.aiport_candidate as candidate
    service, sink = await service_for(tmp_path)
    now = [800.0]
    monkeypatch.setattr(candidate.time, "monotonic", lambda: now[0])
    quiet = pcm(noise(0.3))
    try:
        await service._observe_pool_audio(CAMERA, quiet)                   # announce
        await audio_settings(service, sink, 60, enableAlrmSpeak=0)         # Protect: off
        now[0] += 300
        await service._observe_pool_audio(CAMERA, quiet)
        assert len(sink.events("EventAIPortStatus")) == 1                   # not yet
        now[0] += 301
        await service._observe_pool_audio(CAMERA, quiet)
        assert [s["isAudioEventReady"] for s in sink.events("EventAIPortStatus")] == [True, False, True]
        await audio_settings(service, sink, 61)                            # owner turned it on
        now[0] += 3600
        await service._observe_pool_audio(CAMERA, quiet)
        assert len(sink.events("EventAIPortStatus")) == 3                   # enabled: quiet
    finally:
        await service.stop()

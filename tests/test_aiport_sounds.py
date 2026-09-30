"""Alarm and household sounds on AI Port relay audio (#28). Synthetic PCM and scores only."""

import array
import json
import math

import pytest

from aikey.aiport_audio import AUDIO_TYPES, SAMPLE_RATE, audio_event_payload, parse_audio_flags
from aikey.aiport_candidate import CandidateError, load_config
from aikey.aiport_sounds import SOUND_TYPES, SoundEvents, beep_kind
from test_aiport_candidate import private_file
from test_aiport_speech import (CAMERA, OTHER, Clock, audio_settings, noise, pcm, pool_config,
                                service_for, settings, voice)

PIN = "a" * 64


def beeps(on_s, off_s, count, pause_s, repeats=2, amplitude=9000):
    out = []
    for _ in range(repeats):
        for _ in range(count):
            out += [amplitude * math.sin(2 * math.pi * 3100 * n / SAMPLE_RATE)
                    for n in range(int(on_s * SAMPLE_RATE))]
            out += [0.0] * int(off_s * SAMPLE_RATE)
        out += [0.0] * int(pause_s * SAMPLE_RATE)
    return array.array("h", (int(v) for v in out))


def test_the_t3_and_t4_patterns_tell_smoke_from_co():
    assert beep_kind(beeps(0.5, 0.5, 3, 1.5)) == "alrmSmoke"
    assert beep_kind(beeps(0.1, 0.1, 4, 5.0)) == "alrmCmonx"
    assert beep_kind(array.array("h", [0] * SAMPLE_RATE * 4)) is None


class Scores:
    def __init__(self):
        self.value, self.calls = {}, 0

    def __call__(self, window):
        self.calls += 1
        return dict(self.value)


def detector(scores, clock, enabled=lambda kind: True):
    return SoundEvents(scores, enabled=enabled, clock=clock)


def run(events, clock, seconds, amplitude=6000):
    edges = []
    for _ in range(int(seconds / 0.3)):
        clock.now += 0.3
        edges += [(e.kind, e.edge) for e in events.feed(pcm(noise(0.3, amplitude=amplitude)))]
    return edges


def test_a_sound_enters_after_two_hops_and_leaves_after_its_quiet_time():
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    assert run(events, clock, 2) == []
    scores.value = {"alrmBabyCry": 0.9}
    assert run(events, clock, 2) == [("alrmBabyCry", "enter")]
    scores.value = {}
    assert run(events, clock, 6) == [("alrmBabyCry", "leave")]


def test_a_higher_priority_sound_preempts_and_a_lower_one_waits():
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    scores.value = {"alrmBark": 0.9}
    assert run(events, clock, 2) == [("alrmBark", "enter")]
    scores.value = {"alrmBark": 0.9, "alrmCarHorn": 0.9}
    assert run(events, clock, 1) == []                           # car horn ranks lower
    scores.value = {"alrmBark": 0.9, "alrmSiren": 0.9}
    assert run(events, clock, 2) == [("alrmBark", "leave"), ("alrmSiren", "enter")]


def test_disabled_types_and_quiet_audio_never_enter_or_classify():
    clock, scores = Clock(), Scores()
    events = detector(scores, clock, enabled=lambda kind: kind != "alrmBark")
    scores.value = {"alrmBark": 0.99}
    assert run(events, clock, 3) == []
    run(events, clock, 1.5, amplitude=1)          # the loud window scrolls out
    calls = scores.calls
    assert run(events, clock, 3, amplitude=1) == [] and scores.calls == calls


def test_the_payload_names_one_type_and_every_other_type_reads_none():
    payload = audio_event_payload(CAMERA, "alrmSmoke", "enter", clock_wall_ms=1, level_db=-20)
    assert payload["alrmSmoke"] == "enter"
    assert all(payload[kind] == "none" for kind in AUDIO_TYPES if kind != "alrmSmoke")
    _, flags = parse_audio_flags(settings(enableAlrmSmoke=1))
    assert flags["alrmSmoke"] and flags["alrmSpeak"] and not flags["alrmBark"]


def sound_config(tmp_path, **changes):
    config = pool_config(tmp_path, speech=(CAMERA,))
    config["live_sound"] = {"cameras": [CAMERA], "model_path": "/models/sound.onnx",
                            "model_sha256": PIN, "class_map_path": "/models/classes.csv",
                            "class_map_sha256": PIN, **changes}
    return config


def test_sound_cameras_must_also_be_speech_cameras_with_pinned_models(tmp_path):
    path = tmp_path / "ok.json"
    private_file(path, json.dumps(sound_config(tmp_path)).encode())
    assert load_config(path, check_decoder_executable=False)["live_sound"]["types"] == list(SOUND_TYPES)
    for changes in ({"cameras": [OTHER]}, {"model_sha256": "x"}, {"model_path": "sound.onnx"},
                    {"types": ["alrmSpeak"]}, {"types": []}, {"max_events_per_hour": 0},
                    {"extra": 1}):
        path = tmp_path / "bad.json"
        private_file(path, json.dumps(sound_config(tmp_path, **changes)).encode())
        with pytest.raises(CandidateError):
            load_config(path, check_decoder_executable=False)


async def sound_service(tmp_path, scores, clock):
    from aikey.aiport_candidate import CandidateService
    service, sink = await service_for(tmp_path)
    service2 = CandidateService(sound_config(tmp_path), tmp_path)
    service2._params_agreed, service2._current_ws = True, sink
    service2.ingress.list_streams = service.ingress.list_streams
    service2.ingress.audio_ready = lambda camera: True
    await service.stop()
    service2._sounds = {CAMERA: SoundEvents(
        scores, enabled=lambda kind: service2._sound_enabled(CAMERA, kind), clock=clock)}
    service2._speech[CAMERA]._clock = clock
    return service2, sink


async def feed(service, samples, clock):
    data = pcm(samples)
    for offset in range(0, len(data), 9600):
        clock.now += 0.3
        await service._observe_pool_audio(CAMERA, data[offset:offset + 9600])


async def test_an_enabled_alarm_preempts_open_speech_and_is_announced_as_a_feature(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 40, enableAlrmSmoke=1)
        await feed(service, noise(1) + voice(2), clock)
        assert [e["alrmSpeak"] for e in sink.events("EventSmartAudio")] == ["enter"]
        scores.value = {"alarm": 0.9}
        await feed(service, noise(1.5, amplitude=6000), clock)   # steady: no beep pattern
        audio = sink.events("EventSmartAudio")
        assert [(e["alrmSpeak"], e["alrmSmoke"]) for e in audio] == [
            ("enter", "none"), ("leave", "none"), ("none", "enter")]
        flags = sink.events("EventFeatureFlagsUpdated")[0]["smartDetect"]
        assert {"alrmSpeak", "alrmSmoke", "alrmGlassBreak"} <= set(flags)
        sounds = (await health(service))["sounds"]
        assert sounds["entered"] == {"alrmSmoke": 1} and sounds["speech_preempted"] == 1
    finally:
        await service.stop()


async def test_disabling_an_open_sound_closes_it(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 50, enableAlrmBark=1)
        scores.value = {"alrmBark": 0.9}
        await feed(service, noise(2, amplitude=6000), clock)
        await audio_settings(service, sink, 51, enableAlrmBark=0)
        assert [e["alrmBark"] for e in sink.events("EventSmartAudio")] == ["enter", "leave"]
    finally:
        await service.stop()


async def health(service):
    return json.loads((await service._health(None)).text)


def test_the_classifier_is_pinned_and_maps_audioset_names_to_protect_types(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import SimpleNamespace

    import numpy as np

    from aikey.aiport_sounds import CLASSES, SoundClassifier, SoundError
    names = ["Speech"] + [label for labels in CLASSES.values() for label in labels]
    class_map = tmp_path / "classes.csv"
    class_map.write_text("index,mid,display_name\n" + "".join(
        f'{i},/m/{i},"{name}"\n' for i, name in enumerate(names)))
    model = tmp_path / "sound.onnx"
    model.write_bytes(b"synthetic model bytes")
    top = np.zeros((2, len(names)), dtype=np.float32)
    top[1, names.index("Baby cry, infant cry")] = 0.8
    top[0, names.index("Fire alarm")] = 0.6

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def get_inputs(self):
            return [SimpleNamespace(name="waveform")]

        def run(self, outputs, feeds):
            assert feeds["waveform"].dtype == np.float32
            return [top, np.zeros((2, 1024), dtype=np.float32)]

    options = SimpleNamespace
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=options, InferenceSession=Session))
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()   # noqa: E731
    classify = SoundClassifier(model, digest(model), class_map, digest(class_map))
    scores = classify(array.array("h", [0] * 15600))
    assert scores["alrmBabyCry"] == pytest.approx(0.8) and scores["alarm"] == pytest.approx(0.6)
    assert scores["alrmBark"] == 0.0
    with pytest.raises(SoundError, match="digest"):
        SoundClassifier(model, "0" * 64, class_map, digest(class_map))
    class_map.write_text("index,mid,display_name\n0,/m/0,Speech\n")
    with pytest.raises(SoundError, match="incomplete"):
        SoundClassifier(model, digest(model), class_map, digest(class_map))

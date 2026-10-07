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


def test_two_sounds_open_and_close_independently():
    # Protect adds every entering type to the camera's one audio event.
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    scores.value = {"alrmBark": 0.9}
    assert run(events, clock, 2) == [("alrmBark", "enter")]
    scores.value = {"alrmBark": 0.9, "alrmCarHorn": 0.9}
    assert run(events, clock, 1) == [("alrmCarHorn", "enter")]
    assert events.open_kinds == {"alrmBark", "alrmCarHorn"} and events.open == "alrmBark"
    scores.value = {"alrmBark": 0.9}
    assert run(events, clock, 3) == [("alrmCarHorn", "leave")]
    events.close("alrmBark")
    assert events.open is None


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


async def test_an_alarm_joins_open_speech_in_one_event_and_is_announced_as_a_feature(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 40, enableAlrmSmoke=1)
        await feed(service, noise(1) + voice(2), clock)
        assert [e["alrmSpeak"] for e in sink.events("EventSmartAudio")] == ["enter"]
        scores.value = {"alarm": 0.9}
        await feed(service, list(beeps(0.5, 0.5, 3, 1.5, repeats=1)), clock)   # a T3 pattern
        audio = sink.events("EventSmartAudio")
        assert [(e["alrmSpeak"], e["alrmSmoke"]) for e in audio][:2] == [
            ("enter", "none"), ("moving", "enter")]
        flags = sink.events("EventFeatureFlagsUpdated")[0]["smartDetect"]
        assert {"alrmSpeak", "alrmSmoke", "alrmGlassBreak"} <= set(flags)
        sounds = (await health(service))["sounds"]
        assert sounds["entered"] == {"alrmSmoke": 1} and sounds["joined_speech"] == 1
        assert sounds["combined_events"] == 1
        assert sounds["enabled_types"] == 1                  # only smoke is enabled
        scores.value = {}
        await feed(service, noise(12, amplitude=200), clock)
        last = sink.events("EventSmartAudio")[-1]
        assert all(last[kind] in ("leave", "none") for kind in AUDIO_TYPES)   # the event ends
        assert not service._audio_open
    finally:
        await service.stop()


async def test_speech_does_not_enter_while_a_sound_is_open(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 45, enableAlrmBark=1)
        scores.value = {"alrmBark": 0.9}
        await feed(service, noise(2, amplitude=600), clock)
        await feed(service, voice(2), clock)
        assert all(e["alrmSpeak"] == "none" for e in sink.events("EventSmartAudio"))
        assert service.speech_edges_suppressed >= 1
    finally:
        await service.stop()


async def test_two_sounds_share_one_event_that_ends_when_both_have_left(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 46, enableAlrmBark=1, enableAlrmSiren=1)
        scores.value = {"alrmBark": 0.9}
        await feed(service, noise(1.5, amplitude=6000), clock)
        scores.value = {"alrmBark": 0.9, "alrmSiren": 0.9}
        await feed(service, noise(3, amplitude=6000), clock)
        scores.value = {"alrmSiren": 0.9}
        await feed(service, noise(4, amplitude=6000), clock)
        scores.value = {}
        await feed(service, noise(6, amplitude=6000), clock)
        audio = [(e["alrmBark"], e["alrmSiren"]) for e in sink.events("EventSmartAudio")]
        assert audio == [("enter", "none"), ("moving", "enter"), ("leave", "moving"),
                         ("none", "leave")]
        assert (await health(service))["sounds"]["combined_events"] == 1
    finally:
        await service.stop()


async def test_overlapping_types_cannot_chain_one_event_past_the_cap(tmp_path, monkeypatch):
    import aikey.aiport_candidate as candidate
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    monkeypatch.setattr(candidate.time, "monotonic", clock)
    try:
        await audio_settings(service, sink, 47, enableAlrmBark=1)
        scores.value = {"alrmBark": 0.9}
        await feed(service, noise(1.5, amplitude=6000), clock)
        clock.now += candidate.MAX_EVENT_S
        await feed(service, noise(0.3, amplitude=6000), clock)
        ends = [e for e in sink.events("EventSmartAudio") if e["alrmBark"] == "leave"]
        assert len(ends) == 1 and (await health(service))["sounds"]["events_capped"] == 1
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


async def test_sounds_are_capped_per_camera_and_hour_and_alarms_get_four_times_the_budget(tmp_path):
    clock, scores = Clock(), Scores()
    service, sink = await sound_service(tmp_path, scores, clock)
    service._sound_limit = 2
    try:
        await audio_settings(service, sink, 60, enableAlrmBark=1, enableAlrmSmoke=1)

        async def episode(kind_scores):
            scores.value = kind_scores
            sound = (list(beeps(0.5, 0.5, 3, 1.5, repeats=1)) if "alarm" in kind_scores
                     else noise(1.5, amplitude=6000))
            await feed(service, sound, clock)
            scores.value = {}
            await feed(service, noise(8, amplitude=6000), clock)

        for _ in range(3):
            await episode({"alrmBark": 0.9})
        for _ in range(9):
            await episode({"alarm": 0.9})
        entered = (await health(service))["sounds"]["entered"]
        assert entered == {"alrmBark": 2, "alrmSmoke": 8}
        assert service.sound_rate_limited >= 2
    finally:
        await service.stop()


def test_the_mel_filterbank_matches_yamnets_frontend_shape_and_edges():
    import numpy as np

    from aikey.aiport_sounds import _hz_to_mel, mel_weights
    weights = mel_weights()
    assert weights.shape == (257, 64) and not weights[0].any()        # no DC
    freqs = np.linspace(0, 8000, 257)
    assert not weights[freqs < 125].any() and not weights[freqs > 7500].any()
    peaks = freqs[weights.argmax(axis=0)]
    assert (np.diff(peaks) >= 0).all()                                 # bands rise in pitch
    centers = np.linspace(_hz_to_mel(125.0), _hz_to_mel(7500.0), 66)[1:-1]
    hz = 700 * (np.exp(centers / 1127.0) - 1)
    assert np.all(np.abs(peaks - hz) <= 8000 / 256 + 1e-6)             # peak within one FFT bin


def test_a_tone_lights_its_own_mel_band_and_silence_reads_the_log_floor():
    import numpy as np

    from aikey.aiport_sounds import SoundError, log_mel_patch, mel_weights
    t = np.arange(15600) / SAMPLE_RATE
    patch = log_mel_patch(0.5 * np.sin(2 * np.pi * 1000 * t))
    assert patch.shape == (96, 64) and patch.dtype == np.float32
    band = int(np.bincount(patch.argmax(axis=1)).argmax())
    freqs = np.linspace(0, 8000, 257)
    assert abs(freqs[mel_weights()[:, band].argmax()] - 1000) < 100
    assert np.allclose(log_mel_patch(np.zeros(15600)), np.log(0.001))
    with pytest.raises(SoundError):
        log_mel_patch(np.zeros(16000))


@pytest.mark.parametrize("shape,expected", [
    ([1, 1, 96, 64], (1, 1, 96, 64)), (["batch", 96, 64], (1, 96, 64)),
    ([96, 64], (96, 64)), (["samples"], (15600,))])
def test_patch_models_get_log_mel_patches_and_waveform_models_get_audio(tmp_path, monkeypatch,
                                                                       shape, expected):
    import hashlib
    import sys
    from types import SimpleNamespace

    import numpy as np

    from aikey.aiport_sounds import CLASSES, SoundClassifier
    names = [label for labels in CLASSES.values() for label in labels]
    class_map = tmp_path / "classes.csv"
    class_map.write_text("index,mid,display_name\n" + "".join(
        f'{i},/m/{i},"{name}"\n' for i, name in enumerate(names)))
    model = tmp_path / "sound.onnx"
    model.write_bytes(b"synthetic")
    fed = []

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def get_inputs(self):
            return [SimpleNamespace(name="input", shape=shape)]

        def run(self, outputs, feeds):
            fed.append(feeds["input"])
            return [np.zeros((1, len(names)), dtype=np.float32)]

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=SimpleNamespace, InferenceSession=Session))
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()   # noqa: E731
    SoundClassifier(model, digest(model), class_map, digest(class_map))(array.array("h", [0] * 15600))
    assert fed[0].shape == expected and fed[0].dtype == np.float32


async def test_a_sound_camera_without_enabled_types_is_pulsed_until_protect_pushes_them(tmp_path, monkeypatch):
    # 7.3.70 skipped the settings push for five of eight cameras whose sound
    # types were enabled; speech was on, so nothing asked Protect again.
    import aikey.aiport_candidate as candidate
    now = [800.0]
    monkeypatch.setattr(candidate.time, "monotonic", lambda: now[0])
    service, sink = await sound_service(tmp_path, Scores(), Clock())
    quiet = pcm(noise(0.3))
    try:
        await service._observe_pool_audio(CAMERA, quiet)                  # announce
        await audio_settings(service, sink, 70)                           # speech on, no sounds
        now[0] += 601
        await service._observe_pool_audio(CAMERA, quiet)
        assert [s["isAudioEventReady"] for s in sink.events("EventAIPortStatus")] == [True, False, True]
        await audio_settings(service, sink, 71, enableAlrmSmoke=1)        # the missed push arrives
        now[0] += 3600
        await service._observe_pool_audio(CAMERA, quiet)
        assert len(sink.events("EventAIPortStatus")) == 3                  # enabled: quiet
    finally:
        await service.stop()



def test_alarm_scores_without_a_beep_pattern_and_short_siren_blips_do_not_enter():
    # 30 Sep: appliance tones and road noise produced false alarms and sirens.
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    scores.value = {"alarm": 0.9}
    assert run(events, clock, 3) == []                  # steady noise: no T3/T4 pattern
    scores.value = {"alrmSiren": 0.9}
    assert run(events, clock, 3) == []                  # under ~4 s of siren (5 Oct)
    assert run(events, clock, 1.5) == [("alrmSiren", "enter")]
    scores.value = {}
    run(events, clock, 5)
    scores.value = {"alrmGlassBreak": 0.55}             # below the shatter bar
    assert run(events, clock, 2) == []


def test_a_bark_needs_a_score_of_at_least_0_6():
    # 7 Oct: most false barks scored under 0.6.
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    scores.value = {"alrmBark": 0.55}
    assert run(events, clock, 5) == []
    scores.value = {"alrmBark": 0.65}
    assert run(events, clock, 1.2) == [("alrmBark", "enter")]


def test_a_siren_needs_a_clear_score_held_for_about_four_seconds():
    # 5 Oct: 11 night-time sirens in 6 hours on the road-facing Einfahrt.
    clock, scores = Clock(), Scores()
    events = detector(scores, clock)
    scores.value = {"alrmSiren": 0.65}                  # passed the old 0.6 bar
    assert run(events, clock, 10) == []
    scores.value = {"alrmSiren": 0.9}
    assert run(events, clock, 3) == []                  # the old ~2 s bar
    scores.value = {"alrmSiren": 0.6}                   # a dip restarts the count
    assert run(events, clock, 0.6) == []
    scores.value = {"alrmSiren": 0.9}
    assert run(events, clock, 3) == []
    assert run(events, clock, 1.5) == [("alrmSiren", "enter")]


def synthetic_classifier(tmp_path, monkeypatch, scored):
    """A pinned classifier over a synthetic class map and one score row."""
    import hashlib
    import sys
    from types import SimpleNamespace

    import numpy as np

    from aikey.aiport_sounds import CLASSES, CONFUSERS, SoundClassifier
    names = list(dict.fromkeys(["Speech"] + [label for labels in CLASSES.values() for label in labels]
                               + [label for labels in CONFUSERS.values() for label in labels]))
    class_map = tmp_path / "classes.csv"
    class_map.write_text("index,mid,display_name\n" + "".join(
        f'{i},/m/{i},"{name}"\n' for i, name in enumerate(names)))
    model = tmp_path / "sound.onnx"
    model.write_bytes(b"synthetic model bytes")
    row = np.zeros((1, len(names)), dtype=np.float32)
    for name, value in scored.items():
        row[0, names.index(name)] = value

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def get_inputs(self):
            return [SimpleNamespace(name="waveform")]

        def run(self, outputs, feeds):
            return [row]

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=SimpleNamespace, InferenceSession=Session))
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()   # noqa: E731
    return SoundClassifier(model, digest(model), class_map, digest(class_map)), names


@pytest.mark.parametrize("scored, group, kept", [
    ({"Baby cry, infant cry": 0.8, "Meow": 0.85}, "alrmBabyCry", False),     # the cat
    ({"Baby cry, infant cry": 0.8, "Meow": 0.3}, "alrmBabyCry", True),
    ({"Shatter": 0.7, "Keys jangling": 0.75}, "alrmGlassBreak", False),      # keys at the door
    ({"Shatter": 0.7, "Chink, clink": 0.7}, "alrmGlassBreak", False),
    ({"Shatter": 0.9, "Coin (dropping)": 0.4}, "alrmGlassBreak", True),
    ({"Siren": 0.8, "Meow": 0.9}, "alrmSiren", True),                         # no siren veto
    ({"Bark": 0.7, "Purr": 0.75}, "alrmBark", False),                         # the cat again
    ({"Bark": 0.8, "Cat": 0.3}, "alrmBark", True),
])
def test_a_confuser_class_scoring_as_high_vetoes_the_sound(tmp_path, monkeypatch,
                                                          scored, group, kept):
    classify, names = synthetic_classifier(tmp_path, monkeypatch, scored)
    scores = classify(array.array("h", [0] * 15600))
    raw = max(value for name, value in scored.items() if name not in
              ("Meow", "Purr", "Cat", "Keys jangling", "Chink, clink", "Coin (dropping)"))
    if kept:
        assert scores[group] == pytest.approx(raw) and "veto:" + group not in scores
    else:
        assert scores[group] == 0.0 and scores["veto:" + group] == pytest.approx(raw)
    assert names[int(scores["top"])] == max(scored, key=scored.get)


def test_entered_sounds_count_their_strongest_class_score_and_level_and_vetoes():
    clock = Clock()

    class Named(Scores):
        names = tuple(f"Class {i}" for i in range(10))

    scores = Named()
    events = detector(scores, clock)
    scores.value = {"veto:alrmBabyCry": 0.9}            # a cat: never enters, counted
    assert run(events, clock, 2) == []
    assert events.vetoed["alrmBabyCry"] >= 3 and events.vetoed["alrmGlassBreak"] == 0
    assert events.vetoed["alrmBark"] == 0
    for top in range(8):                                # eight distinct strongest classes
        scores.value = {"alrmBark": 0.85, "top": float(top)}
        assert run(events, clock, 1.2) == [("alrmBark", "enter")]
        scores.value = {}
        run(events, clock, 4)
    detail = events.entered_detail["alrmBark"]
    assert detail["top"] == {**{f"Class {i}": 1 for i in range(6)}, "other": 2}
    assert detail["score"] == {"0.9+": 0, "0.8-0.9": 8, "0.6-0.8": 0, "under_0.6": 0}
    assert sum(detail["level"].values()) == 8
    assert set(detail["level"]) == {"-30+", "-40..-30", "-50..-40", "under_-50"}


async def test_camera_health_counts_vetoes_and_entered_sound_detail_without_identifiers(tmp_path):
    clock = Clock()

    class Named(Scores):
        names = ("Speech", "Bark", "Meow")

    scores = Named()
    service, sink = await sound_service(tmp_path, scores, clock)
    try:
        await audio_settings(service, sink, 60, enableAlrmBark=1, enableAlrmBabyCry=1)
        scores.value = {"veto:alrmBabyCry": 0.9, "top": 2.0}
        await feed(service, noise(1.5, amplitude=6000), clock)
        scores.value = {"alrmBark": 0.95, "top": 1.0}
        await feed(service, noise(1.5, amplitude=6000), clock)
        health = service._speech_camera_health(CAMERA)["sound"]
    finally:
        await service.stop()
    assert health["vetoed"]["alrmBabyCry"] >= 1 and health["vetoed"]["alrmGlassBreak"] == 0
    assert list(health["entered"]) == ["alrmBark"]
    assert health["entered"]["alrmBark"]["top"] == {"Bark": 1}
    assert health["entered"]["alrmBark"]["score"]["0.9+"] == 1
    text = json.dumps(health)
    assert CAMERA not in text and CAMERA.lower() not in text

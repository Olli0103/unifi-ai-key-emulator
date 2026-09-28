"""Per-minute motion and provider-gate diagnostics for a natural visit (#6). Synthetic frames only."""

import json
import sys
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from aikey.aiport_api_detection import ApiDetectionError, ApiObjectDetector
from aikey.aiport_motion import MotionDetector, MotionTimeline, parse_motion_settings
from aikey.aiport_timeline import MinuteHistory
from test_aiport_candidate import fixture_state, private_file

CAMERA = "2A1122334455"
SECRET = "-".join(("synthetic", "provider", "secret"))
FULL = [0, 0, 1000, 0, 1000, 1000, 0, 1000]


def picture(box=None, size=(320, 180)):
    image = Image.new("RGB", size, (40, 40, 40))
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    data = BytesIO()
    image.save(data, format="JPEG", quality=85)
    return data.getvalue()


class Clock:
    def __init__(self, start=1_790_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


def detector(level=50, start=1000):
    return MotionDetector(parse_motion_settings({
        "algoVersion": "beta", "deviceID": CAMERA, "enable": True, "eventMaxDurationMSec": 300_000,
        "bgmodel": "default", "lingerEventStartMSec": start, "lingerEventStopMSec": 2000,
        "zones": {"1": {"coord": FULL, "level": level}}}, camera_mac=CAMERA))


def feed(timeline, motion, frames, clock, *, step=0.5):
    for index, frame in enumerate(frames):
        timeline.record(motion, motion.observe(frame, now=clock.now))
        clock.now += step


# --- history container ------------------------------------------------------

def test_the_history_is_bucketed_per_minute_and_fixed_size():
    clock = Clock()
    history = MinuteHistory(("frames",), maxima=("peak",), latest=("threshold",), size=3, clock=clock)
    for minute in range(5):
        history.count("frames", 2)
        history.maximum("peak", minute)
        history.maximum("peak", 1)
        history.set("threshold", 17)
        clock.now += 60
    buckets = history.snapshot()
    assert [b["minute"] for b in buckets] == [int(1_790_000_000 // 60) + m for m in (2, 3, 4)]
    assert [b["peak"] for b in buckets] == [2, 3, 4] and all(b["frames"] == 2 for b in buckets)
    buckets[0]["frames"] = 99
    assert history.snapshot()[0]["frames"] == 2                       # a copy
    with pytest.raises(KeyError):
        history.count("unknown")


# --- motion stage -------------------------------------------------------------

def test_a_small_edge_figure_is_measured_but_stays_below_the_start_threshold():
    clock, motion, timeline = Clock(), detector(level=50), MotionTimeline(clock=Clock())
    timeline.history.clock = clock
    # About 0.8% of the frame at the right edge, walking: measured, never started.
    frames = [picture()] + [picture((306, 90 - i, 318, 110 - i)) if i % 2 else picture((300, 92, 312, 112))
                            for i in range(10)]
    feed(timeline, motion, frames, clock)
    bucket, = timeline.snapshot()
    assert bucket["threshold_permille"] == 17 and bucket["starts"] == 0
    assert 0 < bucket["peak_permille"] < 17
    assert bucket["near_miss"] + bucket["over_threshold"] <= bucket["frames"]
    assert bucket["over_threshold"] == 0


def test_a_person_sized_move_crosses_the_threshold_and_starts():
    clock, motion, timeline = Clock(), detector(level=50, start=0), MotionTimeline(clock=Clock())
    timeline.history.clock = clock
    feed(timeline, motion, [picture(), picture((120, 40, 190, 160)), picture((150, 40, 220, 160))], clock)
    bucket, = timeline.snapshot()
    assert bucket["peak_permille"] >= bucket["threshold_permille"] == 17
    assert bucket["over_threshold"] >= 1 and bucket["starts"] == 1


def test_a_short_move_is_over_threshold_but_too_short_to_start():
    clock, motion, timeline = Clock(), detector(level=50, start=3000), MotionTimeline(clock=Clock())
    timeline.history.clock = clock
    feed(timeline, motion, [picture(), picture((120, 40, 190, 160))] + [picture()] * 8, clock)
    bucket, = timeline.snapshot()
    assert bucket["over_threshold"] >= 1 and bucket["starts"] == 0   # rejected by the start linger


def test_a_stationary_figure_fades_below_the_threshold():
    clock, motion, timeline = Clock(), detector(level=50, start=0), MotionTimeline(clock=Clock())
    timeline.history.clock = clock
    feed(timeline, motion, [picture()], clock)
    clock.now += 60                                                    # next minute: arrives and stands
    feed(timeline, motion, [picture((120, 40, 190, 160))], clock)
    clock.now += 60                                                    # later minute: still standing
    feed(timeline, motion, [picture((120, 40, 190, 160))] * 20, clock)
    clock.now += 60                                                    # a third minute, still standing
    feed(timeline, motion, [picture((120, 40, 190, 160))] * 5, clock)
    arrive, standing, later = timeline.snapshot()[1:]
    assert arrive["over_threshold"] == 1 and arrive["starts"] == 1
    assert standing["over_threshold"] < standing["frames"]          # the background absorbs it
    assert later["peak_permille"] < later["threshold_permille"]     # a still figure is no longer seen
    assert later["over_threshold"] == later["near_miss"] == 0


def test_the_timeline_survives_a_settings_push_and_counts_it():
    clock = Clock()
    timeline = MotionTimeline(clock=clock)
    first = detector()
    feed(timeline, first, [picture(), picture((120, 40, 190, 160))], clock)
    timeline.settings_reset()                                          # candidate replaces the detector
    second = detector(level=0)
    feed(timeline, second, [picture((120, 40, 190, 160))], clock)
    bucket, = timeline.snapshot()
    assert bucket["settings_resets"] == 1 and bucket["frames"] == 3
    assert bucket["threshold_permille"] == 2                           # the new policy's threshold
    assert second.snapshot()["frames"] == 1                            # the detector itself restarted


def test_a_disabled_policy_is_counted_not_measured():
    clock = Clock()
    timeline = MotionTimeline(clock=clock)
    motion = MotionDetector(parse_motion_settings({
        "algoVersion": "beta", "deviceID": CAMERA, "enable": False, "eventMaxDurationMSec": 300_000,
        "bgmodel": "default", "lingerEventStartMSec": 0, "lingerEventStopMSec": 2000,
        "zones": {"1": {"coord": FULL, "level": 50}}}, camera_mac=CAMERA))
    feed(timeline, motion, [picture(), picture((120, 40, 190, 160))], clock)
    bucket, = timeline.snapshot()
    assert bucket["disabled"] == 2 and bucket["frames"] == 0 and bucket["peak_permille"] is None


# --- provider gate ----------------------------------------------------------------

def _ollama():
    return {"provider": "ollama", "model": "synthetic-vision",
            "base_url": "http://127.0.0.1:11434", "max_output_tokens": 128}


def _reply(text):
    return {"done": True, "message": {"content": text}}


PERSON = json.dumps({"detections": [{"kind": "person", "label": "person", "score": 0.95,
                                     "box": [0.1, 0.1, 0.4, 0.9]}]})
LOW = json.dumps({"detections": [{"kind": "person", "label": "person", "score": 0.3,
                                  "box": [0.1, 0.1, 0.4, 0.9]}]})


def test_the_gate_history_separates_quiet_near_miss_opened_and_each_outcome(tmp_path, monkeypatch):
    monotonic = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic", lambda: monotonic[0])
    replies = [_reply('{"detections":[]}'), _reply(PERSON), _reply(PERSON), _reply(LOW)]
    clock = Clock()
    gate = ApiObjectDetector(_ollama(), tmp_path, threshold=0.8, clock=clock,
                             transport=lambda *_: replies.pop(0))
    still, small, big = picture(), picture((300, 90, 306, 100)), picture((100, 20, 220, 170))
    gate.detect_for_camera(CAMERA, still)                              # startup probe: empty
    for _ in range(3):
        monotonic[0] += 0.5
        gate.detect_for_camera(CAMERA, still)                          # quiet; re-arms
    monotonic[0] += 0.5
    gate.detect_for_camera(CAMERA, small)                              # tiny change: not opened
    monotonic[0] += 0.5
    gate.detect_for_camera(CAMERA, big)                                # opened: objects
    monotonic[0] += 0.5
    gate.detect_for_camera(CAMERA, big)                                # confirming request: objects
    bucket, = gate.gate_history(CAMERA)
    assert bucket["threshold_cells"] == 8
    assert bucket["frames"] == 7 and bucket["opened"] == 3 and bucket["requests"] == 3
    assert (bucket["empty"], bucket["objects"], bucket["low"], bucket["failed"]) == (1, 2, 0, 0)
    assert bucket["peak_cells"] >= 8 and bucket["over_threshold"] >= 1
    assert bucket["quiet"] + bucket["near_miss"] + bucket["over_threshold"] == 6   # baseline not measured


def test_failures_and_back_off_skips_are_their_own_categories(tmp_path, monkeypatch):
    monotonic = [100.0]
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic", lambda: monotonic[0])

    def fail(*_):
        raise ApiDetectionError("api_detection_http_429")
    gate = ApiObjectDetector(_ollama(), tmp_path, threshold=0.8, clock=Clock(), transport=fail)
    still, big = picture(), picture((100, 20, 220, 170))
    with pytest.raises(ApiDetectionError):
        gate.detect_for_camera(CAMERA, still)                          # startup probe fails
    for frame in (still, still, still, big):                           # re-arm, then motion in the pause
        monotonic[0] += 0.5
        gate.detect_for_camera(CAMERA, frame)
    bucket, = gate.gate_history(CAMERA)
    assert bucket["requests"] == 1 and bucket["failed"] == 1 and bucket["backoff_skipped"] == 1


def test_the_histories_hold_only_minutes_and_integers(tmp_path, monkeypatch):
    monkeypatch.setattr("aikey.aiport_api_detection.time.monotonic", lambda: 100.0)
    gate = ApiObjectDetector(_ollama(), tmp_path, threshold=0.8, clock=Clock(),
                             transport=lambda *_: _reply(json.dumps(
                                 {"detections": [], "note": SECRET})))
    with pytest.raises(ApiDetectionError):
        gate.detect_for_camera(CAMERA, picture())                      # untrusted extra field
    timeline = MotionTimeline(clock=Clock())
    feed(timeline, detector(), [picture(), picture((120, 40, 190, 160))], Clock())
    for history in (gate.gate_history(CAMERA), timeline.snapshot()):
        text = json.dumps(history)
        assert SECRET not in text and CAMERA not in text and "http" not in text
        assert all(type(v) in (int, type(None)) for bucket in history for v in bucket.values())


@pytest.mark.asyncio
async def test_the_candidate_keeps_one_timeline_per_camera_across_motion_settings(tmp_path):
    from aikey.aiport_candidate import CandidateService
    camera = "2A1122334455"
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{"camera_mac": camera, "source_ip": "192.168.10.1",
                                 "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8, "smart_types": ["person"],
        "max_events_per_hour": 12, "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1", "allow_remote": True,
                            "max_output_tokens": 256, "api_key_file": str(tmp_path / "api-key")}}
    service = CandidateService(config, tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": camera}]
    service._inference = None                                          # motion stage only; no provider

    class Sink:
        def __init__(self):
            self.messages = []

        async def send_bytes(self, raw):
            self.messages.append(json.loads(raw))

    sink = Sink()
    service._current_ws = sink
    command = {"functionName": "ChangeSmartMotionSettings", "messageId": 8,
               "payload": {"algoVersion": "beta", "deviceID": camera, "enable": True,
                           "eventMaxDurationMSec": 300000, "bgmodel": "default",
                           "lingerEventStartMSec": 0, "lingerEventStopMSec": 1000,
                           "zones": {"1": {"coord": FULL, "level": 50}}}}
    try:
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        await service._observe_pool_frame(camera, picture())
        await service._observe_pool_frame(camera, picture((120, 40, 190, 160)))
        command["messageId"] = 9
        await service._handle_diagnostic_frame(sink, json.dumps(command).encode())
        await service._observe_pool_frame(camera, picture((120, 40, 190, 160)))
        buckets = service._pool_motion_timeline[camera].snapshot()
        total = {key: sum(b[key] for b in buckets) for key in ("frames", "starts", "stops", "settings_resets")}
        assert total == {"frames": 3, "starts": 1, "stops": 1, "settings_resets": 1}
        assert service._pool_motion[camera].snapshot()["frames"] == 1  # new detector after the push
    finally:
        await service.stop()

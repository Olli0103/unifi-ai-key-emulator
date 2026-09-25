"""Synthetic zone-scoped motion for AI Port paired cameras."""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from aikey.aiport_motion import (MotionDetector, MotionSettingsError,
                                 motion_event_payload, parse_motion_settings)


CAMERA = "2A1122334455"
LEFT_HALF = [0, 0, 500, 0, 500, 1000, 0, 1000]


def settings(*, enable=True, zones=None, start=1000, stop=2000):
    return {"algoVersion": "beta", "deviceID": CAMERA, "enable": enable,
            "eventMaxDurationMSec": 300_000, "bgmodel": "default",
            "lingerEventStartMSec": start, "lingerEventStopMSec": stop,
            "zones": zones if zones is not None else {
                "1": {"coord": LEFT_HALF, "level": 50, "triggerLight": True}}}


def frame(box=None):
    image = Image.new("RGB", (320, 180), (40, 40, 40))
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=(230, 230, 230))
    data = BytesIO()
    image.save(data, format="JPEG", quality=85)
    return data.getvalue()


STILL = frame()
IN_ZONE = frame((40, 40, 110, 160))
OUT_OF_ZONE = frame((210, 40, 280, 160))


def test_parses_exact_envelope_and_rejects_other_camera_or_geometry():
    policy = parse_motion_settings(settings(), camera_mac=CAMERA)
    assert policy.enabled and policy.linger_start_ms == 1000
    assert [(zone.zone_id, zone.level) for zone in policy.zones] == [(1, 50)]
    with pytest.raises(MotionSettingsError, match="wrong_camera"):
        parse_motion_settings(settings(), camera_mac="2A1122334456")
    for zone in ({"coord": [0, 0, 1000], "level": 50},
                 {"coord": LEFT_HALF, "level": 101},
                 {"coord": LEFT_HALF, "level": 50, "extra": 1}):
        with pytest.raises(MotionSettingsError):
            parse_motion_settings(settings(zones={"1": zone}), camera_mac=CAMERA)
    raw = settings()
    raw["unknown"] = True
    with pytest.raises(MotionSettingsError):
        parse_motion_settings(raw, camera_mac=CAMERA)


def test_motion_starts_after_linger_only_inside_its_zone_and_stops():
    detector = MotionDetector(parse_motion_settings(settings(), camera_mac=CAMERA))
    assert detector.observe(STILL, now=0) == ()
    # Change outside the zone never starts motion.
    for step in range(4):
        image = OUT_OF_ZONE if step % 2 == 0 else STILL
        assert detector.observe(image, now=0.5 + step * 0.5) == ()
    assert not detector.active
    detector = MotionDetector(parse_motion_settings(settings(), camera_mac=CAMERA))
    detector.observe(STILL, now=0)
    assert detector.observe(IN_ZONE, now=1.0) == ()          # linger 1 s
    assert detector.observe(IN_ZONE, now=1.5) == ()
    start, = detector.observe(IN_ZONE, now=2.0)
    assert start.edge == "start" and set(start.levels) == {"1"}
    edges = ()
    now = 2.5
    while not edges:
        edges = detector.observe(STILL, now=now)
        now += 0.5
        assert now < 30
    stop, = edges
    assert stop.edge == "stop"
    assert detector.snapshot()["starts"] == detector.snapshot()["stops"] == 1


def test_whole_frame_change_and_disabled_policy_do_not_start_motion():
    detector = MotionDetector(parse_motion_settings(
        settings(start=0), camera_mac=CAMERA))
    detector.observe(frame(), now=0)
    bright = BytesIO()
    Image.new("RGB", (320, 180), (240, 240, 240)).save(bright, format="JPEG")
    assert detector.observe(bright.getvalue(), now=0.5) == ()
    assert detector.snapshot()["scene_changes"] == 1
    disabled = MotionDetector(parse_motion_settings(
        settings(enable=False, start=0), camera_mac=CAMERA))
    for index, image in enumerate((STILL, IN_ZONE, STILL, IN_ZONE)):
        assert disabled.observe(image, now=index) == ()


def test_motion_payload_matches_protects_required_fields():
    detector = MotionDetector(parse_motion_settings(settings(start=0), camera_mac=CAMERA))
    detector.observe(STILL, now=0)
    start, = detector.observe(IN_ZONE, now=0.5)
    payload = motion_event_payload(CAMERA, start, clock_wall_ms=1_700_000_000_000)
    assert {"clockBestMonotonic", "clockBestWall", "clockMonotonic", "clockStream",
            "clockStreamRate", "clockWall", "edgeType", "eventId", "eventType",
            "motionHeatmap", "motionSnapshot"} <= set(payload)
    assert (payload["eventType"], payload["edgeType"]) == ("motion", "start")
    assert payload["deviceID"] == CAMERA

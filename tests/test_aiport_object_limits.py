"""Which limits an AI Port applies to object detection (owner decision, 30 Sep 2026).

Synthetic configs only. Object events have no per-camera ceiling; a paid
request cap is an optional cost guard; local fallback is bounded by compute
backpressure, not a count; speech keeps its own separate limit.
"""

import json

import pytest

from aikey.aiport_candidate import CandidateError, CandidateService, load_config
from test_aiport_candidate import private_file
from test_aiport_detection_fallback import FALLBACK
from test_aiport_speech import CAMERA, health, pool_config


def load(tmp_path, config, name="config.json"):
    path = tmp_path / name
    private_file(path, json.dumps(config).encode())
    return load_config(path, check_decoder_executable=False)


def test_a_live_pool_needs_no_object_event_ceiling(tmp_path):
    config = pool_config(tmp_path)
    del config["live_pool_detector"]["max_events_per_hour"]
    loaded = load(tmp_path, config)
    service = CandidateService(loaded, tmp_path)
    assert service._camera_engine._max_events is None
    assert service._event_budget is None                       # no durable event budget
    assert service._package_cooldown is not None               # duplicate-parcel rule stays


async def test_a_legacy_event_and_fallback_cap_are_accepted_but_not_applied(tmp_path):
    config = pool_config(tmp_path)                             # carries max_events_per_hour 12
    config["live_pool_detector"]["fallback"] = dict(FALLBACK, max_per_hour=120)
    service = CandidateService(load(tmp_path, config), tmp_path)
    try:
        assert service._camera_engine._max_events is None
        assert (await health(service))["legacy_limits_ignored"] == [
            "live_pool_detector.max_events_per_hour", "live_pool_detector.fallback.max_per_hour"]
    finally:
        await service.stop()


@pytest.mark.parametrize("value", [0, "12", 3601])
def test_a_malformed_legacy_event_cap_is_still_refused(tmp_path, value):
    config = pool_config(tmp_path)
    config["live_pool_detector"]["max_events_per_hour"] = value
    with pytest.raises(CandidateError):
        load(tmp_path, config)


def test_speech_keeps_its_own_limit_independent_of_object_events(tmp_path):
    from aikey.aiport_candidate import SPEECH_EVENTS_PER_HOUR
    config = pool_config(tmp_path)
    del config["live_pool_detector"]["max_events_per_hour"]
    service = CandidateService(load(tmp_path, config), tmp_path)
    assert SPEECH_EVENTS_PER_HOUR == 60 and CAMERA in service._speech
    assert service._speech_limit == 60


def test_the_speech_limit_is_set_per_ai_port(tmp_path):
    config = pool_config(tmp_path)
    config["live_speech_max_events_per_hour"] = 240
    service = CandidateService(load(tmp_path, config), tmp_path)
    assert service._speech_limit == 240


@pytest.mark.parametrize("value", [0, 3601, "60", 1.5, True])
def test_a_malformed_speech_limit_is_refused(tmp_path, value):
    config = pool_config(tmp_path)
    config["live_speech_max_events_per_hour"] = value
    with pytest.raises(CandidateError):
        load(tmp_path, config)


def test_a_speech_limit_needs_speech_cameras(tmp_path):
    config = pool_config(tmp_path)
    del config["live_speech_cameras"]
    config.pop("live_sound", None)
    config["live_speech_max_events_per_hour"] = 60
    with pytest.raises(CandidateError):
        load(tmp_path, config)


def test_camera_thresholds_are_validated_for_paired_cameras_only(tmp_path):
    config = pool_config(tmp_path)
    config["live_pool_detector"]["camera_thresholds"] = {CAMERA: 0.4}
    loaded = load(tmp_path, config)
    assert list(loaded["live_pool_detector"]["camera_thresholds"].values()) == [0.4]
    for bad in ({CAMERA: 0}, {CAMERA: 2}, {CAMERA: True}, {"2A9988776655": 0.4}, {}):
        config["live_pool_detector"]["camera_thresholds"] = bad
        with pytest.raises(CandidateError):
            load(tmp_path, config, name="bad.json")

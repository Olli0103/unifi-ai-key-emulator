"""Native smart policy must not silently enable unsupported camera settings."""

import pytest

from aikey.aiport_smart_settings import SmartSettingsError, parse_smart_settings


CAMERA = "2A1122334455"


def full_frame_policy():
    return {
        "deviceID": CAMERA,
        "algoVersion": "beta",
        "enableSmartDetect": ["person", "vehicle"],
        "eventStartMSec": 1000,
        "eventStopMSec": 3000,
        "zones": {}, "secondLensZones": {}, "lines": {},
        "loiterZones": {}, "excludeZones": {}, "intelligenceZones": {},
        "enableTamperDetection": False,
        "recognitionAccuracy": {"face": 80, "licensePlate": 80},
    }


def test_accepts_camera_bound_full_frame_policy_without_retaining_raw_payload():
    raw = full_frame_policy()
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.camera_mac == CAMERA
    assert policy.allows("person") and policy.allows("vehicle")
    assert not policy.allows("animal") and not policy.allows("face")
    assert (policy.event_start_ms, policy.event_stop_ms) == (1000, 3000)
    raw["enableSmartDetect"].append("animal")
    assert not policy.allows("animal")
    assert not hasattr(policy, "zones")


@pytest.mark.parametrize("field,value,error", [
    ("deviceID", "2A1122334456", "wrong_camera"),
    ("enableSmartDetect", ["face"], "unsupported_smart_feature"),
    ("enableSmartDetect", ["person", "person"], "invalid_smart_settings"),
    ("zones", {"1": {"coord": [0, 0, 1000, 0, 1000, 1000]}},
     "unsupported_smart_feature"),
    ("excludeZones", {"2": {"coord": [0, 0, 1000, 0, 1000, 1000]}},
     "unsupported_smart_feature"),
    ("lines", {"1": {}}, "unsupported_smart_feature"),
    ("enableTamperDetection", True, "unsupported_smart_feature"),
    ("enableTamperDetection", 0, "unsupported_smart_feature"),
    ("eventStartMSec", True, "invalid_smart_settings"),
    ("eventStopMSec", 120_001, "invalid_smart_settings"),
    ("recognitionAccuracy", {"face": float("nan")}, "invalid_smart_settings"),
])
def test_rejects_wrong_camera_or_unenforced_policy(field, value, error):
    raw = full_frame_policy()
    raw[field] = value
    with pytest.raises(SmartSettingsError, match=error):
        parse_smart_settings(raw, camera_mac=CAMERA)


def test_rejects_unknown_fields_and_nested_nonempty_features():
    raw = full_frame_policy()
    raw["privateURL"] = "must-not-be-kept"
    with pytest.raises(SmartSettingsError, match="invalid_smart_settings"):
        parse_smart_settings(raw, camera_mac=CAMERA)
    raw.pop("privateURL")
    raw["reVerificationPolicy"] = {"enable": True}
    with pytest.raises(SmartSettingsError, match="unsupported_smart_feature"):
        parse_smart_settings(raw, camera_mac=CAMERA)

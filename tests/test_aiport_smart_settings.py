"""Native smart policy must not silently enable unsupported camera settings."""

import pytest

from aikey.aiport_smart_settings import (
    SmartSettingsError, parse_motion_probe, parse_smart_settings,
    summarize_smart_request,
)


def test_probe_shape_omits_camera_and_nested_policy_contents():
    private_name = "private-bedroom-zone"
    payload = {"deviceID": "2A1122334455", "algoVersion": "beta",
               "enableSmartDetect": ["person", "face"],
               "eventStartMSec": 100, "eventStopMSec": 200,
               "zones": {private_name: {"points": [[123, 456]]}},
               "lines": {}, "reVerificationPolicy": {"secret": private_name},
               private_name: "not-a-protocol-field"}
    shape = summarize_smart_request(payload, camera_mac="2A:11:22:33:44:55")
    assert shape["camera_matches"] is True
    assert shape["zones_count"] == 1
    assert shape["lines_count"] == 0
    assert shape["enabled_count"] == 2
    assert shape["unknown_fields"] == 1
    assert shape["reVerificationPolicy_configured"] is True
    assert private_name not in str(shape)
    assert "2A1122334455" not in str(shape)


def test_probe_shape_handles_non_object_without_retaining_it():
    assert summarize_smart_request(["private"], camera_mac="2A1122334455") == {
        "object": False}


def test_motion_probe_accepts_only_bound_old_envelope():
    payload = {"algoVersion": "beta", "deviceID": "2A1122334455",
               "enable": True, "eventMaxDurationMSec": 600_000,
               "bgmodel": "default", "lingerEventStartMSec": 1000,
               "lingerEventStopMSec": 3000,
               "zones": {"private-zone-name": {"private-coordinates": [3, 4]}}}
    assert parse_motion_probe(payload, camera_mac="2A:11:22:33:44:55") == 1
    with pytest.raises(SmartSettingsError):
        parse_motion_probe({**payload, "deviceID": "2A1122334456"},
                           camera_mac="2A1122334455")
    with pytest.raises(SmartSettingsError):
        parse_motion_probe({**payload, "eventMaxDurationMSec": True},
                           camera_mac="2A1122334455")
    with pytest.raises(SmartSettingsError):
        parse_motion_probe({**payload, "unknown": "private"},
                           camera_mac="2A1122334455")


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


def test_person_zone_policy_requires_ninety_percent_box_overlap():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person"]
    raw["zones"] = {"7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                          "objectTypes": ["person"], "sensitivity": 50,
                          "triggerLight": True, "triggerAccessTypes": []}}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.zones_configured
    assert policy.person_zone_ids((0.2, 0.2, 0.5, 0.8)) == (7,)
    assert policy.person_zone_ids((0.05, 0.2, 0.5, 0.8)) is None
    raw["zones"]["7"]["coord"][:] = [0, 0, 1000, 0, 1000, 1000]
    assert policy.person_zone_ids((0.2, 0.2, 0.5, 0.8)) == (7,)


def test_zone_only_person_policy_is_scoped_to_that_zone():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = []
    raw["zones"] = {"7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                          "objectTypes": ["person"], "triggerAccessTypes": []}}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.enabled_types == frozenset({"person"})
    assert policy.person_zone_ids((0.2, 0.2, 0.5, 0.8)) == (7,)
    assert policy.person_zone_ids((0.05, 0.2, 0.5, 0.8)) is None
    raw["zones"] = {}
    assert parse_smart_settings(raw, camera_mac=CAMERA).enabled_types == frozenset()


def test_zone_only_mixed_classes_stay_bound_to_their_validated_zones():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = []
    polygon = [100, 100, 900, 100, 900, 900, 100, 900]
    raw["zones"] = {
        str(index): {"coord": polygon, "objectTypes": [kind],
                     "triggerAccessTypes": []}
        for index, kind in enumerate(("person", "vehicle", "animal"), 7)}
    raw["zones"]["10"] = {"coord": polygon, "objectTypes": ["face"],
                          "triggerAccessTypes": []}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.enabled_types == frozenset({"person", "vehicle", "animal"})
    for index, kind in enumerate(("person", "vehicle", "animal"), 7):
        assert policy.zone_ids(kind, (0.2, 0.2, 0.5, 0.8)) == (index,)
        assert policy.zone_ids(kind, (0.05, 0.2, 0.5, 0.8)) is None
    assert not policy.allows("face")
    assert policy.zone_ids("face", (0.2, 0.2, 0.5, 0.8)) is None


def test_accepts_only_explicitly_disabled_reverification_policy():
    raw = full_frame_policy()
    raw["reVerificationPolicy"] = {
        kind: {"enable": False} for kind in ("person", "vehicle", "animal")}
    assert parse_smart_settings(raw, camera_mac=CAMERA).allows("person")
    raw["reVerificationPolicy"]["person"]["enable"] = 0
    with pytest.raises(SmartSettingsError, match="unsupported_smart_feature"):
        parse_smart_settings(raw, camera_mac=CAMERA)


def test_ignores_reverification_for_detection_classes_not_requested():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person"]
    raw["reVerificationPolicy"] = {
        "person": {"enable": False},
        "vehicle": {"enable": True, "mode": "custom",
                    "minPresenceProbability": 40,
                    "maxPresenceProbability": 80},
        "animal": {"enable": True, "mode": "custom",
                   "minPresenceProbability": 40,
                   "maxPresenceProbability": 80},
    }
    assert parse_smart_settings(raw, camera_mac=CAMERA).allows("person")
    raw["reVerificationPolicy"]["person"]["enable"] = True
    with pytest.raises(SmartSettingsError, match="unsupported_smart_feature"):
        parse_smart_settings(raw, camera_mac=CAMERA)


def test_person_reverification_suppresses_uncertain_confidence():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person"]
    raw["reVerificationPolicy"] = {
        "person": {"enable": True, "mode": "custom",
                   "minPresenceProbability": 40,
                   "maxPresenceProbability": 80},
        "vehicle": {"enable": False}, "animal": {"enable": False},
    }
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.person_reverification_ceiling == 0.8
    assert not policy.allows_person_score(0.79)
    assert not policy.allows_person_score(0.8)
    assert policy.allows_person_score(0.81)
    assert not policy.allows_person_score(float("nan"))
    for invalid in (True, 80.0, -1, 101):
        raw["reVerificationPolicy"]["person"]["maxPresenceProbability"] = invalid
        with pytest.raises(SmartSettingsError, match="unsupported_smart_feature"):
            parse_smart_settings(raw, camera_mac=CAMERA)


def test_vehicle_zone_and_reverification_gate_do_not_admit_other_classes():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["vehicle"]
    raw["zones"] = {"9": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                          "objectTypes": ["vehicle"], "triggerAccessTypes": []}}
    raw["reVerificationPolicy"] = {
        "person": {"enable": False},
        "vehicle": {"enable": True, "mode": "custom",
                    "minPresenceProbability": 40, "maxPresenceProbability": 80},
        "animal": {"enable": False},
    }
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert not policy.allows_score("vehicle", 0.8)
    assert policy.allows_score("vehicle", 0.81)
    assert policy.zone_ids("vehicle", (0.2, 0.2, 0.5, 0.8)) == (9,)
    assert policy.zone_ids("vehicle", (0.05, 0.2, 0.5, 0.8)) is None
    assert policy.zone_ids("person", (0.2, 0.2, 0.5, 0.8)) is None


def test_accepts_deprecated_read_only_auto_recognition_precision():
    raw = full_frame_policy()
    raw["recognitionAccuracy"] = {"face": "auto", "licensePlate": "auto"}
    assert parse_smart_settings(raw, camera_mac=CAMERA).allows("person")
    raw["recognitionAccuracy"]["face"] = "high"
    with pytest.raises(SmartSettingsError, match="invalid_smart_settings"):
        parse_smart_settings(raw, camera_mac=CAMERA)


@pytest.mark.parametrize("field,value,error", [
    ("deviceID", "2A1122334456", "wrong_camera"),
    ("enableSmartDetect", ["face"], "unsupported_smart_feature"),
    ("enableSmartDetect", ["person", "person"], "invalid_smart_settings"),
    ("zones", {"1": {"coord": [0, 0, 1000, 0, 1000, 1000]}},
     "invalid_smart_zone"),
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

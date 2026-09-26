"""Native smart policy must not silently enable unsupported camera settings."""

import pytest

from aikey.aiport_smart_settings import (
    SmartSettingsError, parse_motion_probe, parse_smart_settings,
    summarize_recognition_accuracy, summarize_secondary_lens_zones,
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


def test_secondary_lens_summary_keeps_only_bounded_shape_and_classes():
    private_id = "private-garage-zone"
    raw = {"secondLensZones": {private_id: {
        "coord": [0, 0, 1000, 0, 1000, 1000],
        "objectTypes": ["person"], "secret": "private-value"}}}
    shape = summarize_secondary_lens_zones(raw)
    assert shape["zone_count"] == 1
    assert shape["schema_valid"] is False
    assert shape["person_selected"] is True
    assert shape["vehicle_selected"] is False
    assert private_id not in str(shape)
    assert "private-value" not in str(shape)
    assert "1000" not in str(shape)


def test_accuracy_summary_keeps_only_bounded_value_categories():
    private_key = "private-accuracy-key"
    private_value = "private-accuracy-value"
    shape = summarize_recognition_accuracy({"recognitionAccuracy": {
        "face": private_value, "licensePlate": None, private_key: {"secret": 99}}})
    assert shape == {"object": True, "key_count": 3,
                     "unknown_key_count": 1, "face": "other_string",
                     "licensePlate": "null"}
    assert private_key not in str(shape)
    assert private_value not in str(shape)
    assert "99" not in str(shape)


@pytest.mark.parametrize("value,category", [
    (True, "bool"), (float("nan"), "number_out_of_range"),
    (float("inf"), "number_out_of_range"),
    (10 ** 1000, "number_out_of_range"),
    ([], "other"), (None, "null"),
])
def test_accuracy_summary_handles_invalid_values_without_raising(value, category):
    shape = summarize_recognition_accuracy({"recognitionAccuracy": {"face": value}})
    assert shape["face"] == category


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


def test_ignores_valid_empty_secondary_lens_placeholder_only():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person", "vehicle", "animal"]
    raw["secondLensZones"] = {"7": {
        "coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": []}}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.enabled_types == frozenset({"person", "vehicle", "animal"})
    assert not policy.zones_configured
    assert policy.smart_zones == ()


@pytest.mark.parametrize("classes", [
    ["person"], ["face"], ["licensePlate"], ["package", "person"],
])
def test_rejects_secondary_lens_requested_classes(classes):
    raw = full_frame_policy()
    raw["secondLensZones"] = {"7": {
        "coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": classes}}
    with pytest.raises(SmartSettingsError, match="^unsupported_smart_feature:regions:secondLensZones$"):
        parse_smart_settings(raw, camera_mac=CAMERA)


@pytest.mark.parametrize("zone", [
    None,
    {"coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": "person"},
    {"coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": [], "unknown": True},
])
def test_rejects_malformed_secondary_lens_placeholder(zone):
    raw = full_frame_policy()
    raw["secondLensZones"] = {"7": zone}
    with pytest.raises(SmartSettingsError, match="^unsupported_smart_feature:regions:secondLensZones$"):
        parse_smart_settings(raw, camera_mac=CAMERA)


def test_secondary_lens_placeholder_never_enables_empty_policy():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = []
    raw["secondLensZones"] = {"7": {
        "coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": []}}
    assert not parse_smart_settings(raw, camera_mac=CAMERA).enabled_types


def test_secondary_lens_placeholder_does_not_change_primary_person_zone():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person"]
    raw["zones"] = {"3": {
        "coord": [0, 0, 1000, 0, 1000, 1000, 0, 1000],
        "objectTypes": ["person"]}}
    raw["secondLensZones"] = {"7": {
        "coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": []}}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.zone_ids("person", (0.2, 0.2, 0.5, 0.8)) == (3,)


@pytest.mark.parametrize("other", [["face"], ["person"]])
def test_rejects_mixed_secondary_lens_placeholders_and_requests(other):
    raw = full_frame_policy()
    raw["secondLensZones"] = {
        "7": {"coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": []},
        "8": {"coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": other},
    }
    with pytest.raises(SmartSettingsError, match="^unsupported_smart_feature:regions:secondLensZones$"):
        parse_smart_settings(raw, camera_mac=CAMERA)


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


def test_package_policy_is_scoped_to_validated_primary_zone():
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["package"]
    raw["zones"] = {"7": {"coord": [100, 100, 900, 100, 900, 900, 100, 900],
                          "objectTypes": ["package"], "triggerAccessTypes": []}}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.enabled_types == frozenset({"package"})
    assert policy.zone_ids("package", (0.2, 0.2, 0.5, 0.8)) == (7,)
    assert policy.zone_ids("package", (0.05, 0.2, 0.5, 0.8)) is None
    assert not policy.allows("person")


def test_exclude_zone_suppresses_only_its_selected_class():
    raw = full_frame_policy()
    raw["excludeZones"] = {"4": {
        "coord": [450, 100, 550, 100, 550, 900, 450, 900],
        "objectTypes": ["person"], "patrolSetID": -1,
    }}
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert policy.person_zone_ids((0.4, 0.2, 0.5, 0.8)) is None
    assert policy.person_zone_ids((0.2, 0.2, 0.4, 0.8)) == ()
    assert policy.zone_ids("vehicle", (0.4, 0.2, 0.5, 0.8)) == ()


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
    with pytest.raises(SmartSettingsError,
                       match="^invalid_smart_settings:recognition_accuracy$"):
        parse_smart_settings(raw, camera_mac=CAMERA)


@pytest.mark.parametrize("field,value,error", [
    ("deviceID", "2A1122334456", "wrong_camera"),
    ("enableSmartDetect", ["face"], "unsupported_smart_feature:types"),
    ("enableSmartDetect", ["person", "person"],
     "invalid_smart_settings:enabled_types"),
    ("zones", {"1": {"coord": [0, 0, 1000, 0, 1000, 1000]}},
     "invalid_smart_zone"),
    ("excludeZones", {"2": {"coord": [0, 0, 1000, 0, 1000, 1000]}},
     "invalid_exclude_zone"),
    ("lines", {"1": {}}, "unsupported_smart_feature:regions:lines"),
    ("accessTrigger", True, "unsupported_smart_feature:advanced"),
    ("enableTamperDetection", True, "unsupported_smart_feature:tamper"),
    ("enableTamperDetection", 0, "unsupported_smart_feature:tamper"),
    ("eventStartMSec", True, "invalid_smart_settings:timing"),
    ("eventStopMSec", 120_001, "invalid_smart_settings:timing"),
    ("recognitionAccuracy", {"face": float("nan")},
     "invalid_smart_settings:recognition_accuracy"),
])
def test_rejects_wrong_camera_or_unenforced_policy(field, value, error):
    raw = full_frame_policy()
    raw[field] = value
    with pytest.raises(SmartSettingsError) as caught:
        parse_smart_settings(raw, camera_mac=CAMERA)
    assert str(caught.value) == error


def test_rejects_unknown_fields_and_nested_nonempty_features():
    raw = full_frame_policy()
    raw["privateURL"] = "must-not-be-kept"
    with pytest.raises(SmartSettingsError, match="invalid_smart_settings"):
        parse_smart_settings(raw, camera_mac=CAMERA)
    raw.pop("privateURL")
    raw["reVerificationPolicy"] = {"enable": True}
    with pytest.raises(SmartSettingsError, match="unsupported_smart_feature"):
        parse_smart_settings(raw, camera_mac=CAMERA)


def test_package_lens_zones_stay_with_the_doorbell():
    # Protect sends one payload to both the AI Port and a package-lens
    # doorbell, and accepts the doorbell's own packageDetected while paired.
    raw = full_frame_policy()
    raw["enableSmartDetect"] = ["person", "package"]
    raw["zones"] = {"3": {"coord": [0, 0, 1000, 0, 1000, 1000, 0, 1000],
                          "objectTypes": ["person"]}}
    raw["secondLensZones"] = {
        "7": {"coord": [0, 0, 1000, 0, 1000, 1000], "objectTypes": []},
        "8": {"coord": [100, 100, 900, 100, 900, 900], "objectTypes": ["package"]},
    }
    policy = parse_smart_settings(raw, camera_mac=CAMERA)
    assert [zone.object_types for zone in policy.secondary_lens_zones] == [
        frozenset({"package"})]
    assert policy.zone_ids("person", (0.2, 0.2, 0.5, 0.8)) == (3,)
    # Haustür's shape: Package only on the package lens. That lens owns it;
    # the main lens never announces Package (every native doorbell Package
    # was a one-shot package-lens event without a box).
    assert policy.package_scope == "second_lens"
    assert policy.zone_ids("package", (0.2, 0.2, 0.5, 0.8)) is None
    assert policy.zones_for("package") == ()
    # A Package zone drawn on the main lens is the user's explicit choice.
    raw["zones"]["3"]["objectTypes"] = ["person", "package"]
    explicit = parse_smart_settings(raw, camera_mac=CAMERA)
    assert explicit.package_scope == "package_zone"
    assert explicit.zone_ids("package", (0.2, 0.2, 0.5, 0.8)) == (3,)
    # Without a package lens, Package keeps the drawn detection area.
    del raw["secondLensZones"]
    raw["zones"]["3"]["objectTypes"] = ["person"]
    assert parse_smart_settings(raw, camera_mac=CAMERA).package_scope == "detection_area"


def test_malformed_package_lens_zone_still_rejects_policy():
    raw = full_frame_policy()
    raw["secondLensZones"] = {"8": {"coord": [100, 100], "objectTypes": ["package"]}}
    with pytest.raises(SmartSettingsError, match="^unsupported_smart_feature:regions:secondLensZones$"):
        parse_smart_settings(raw, camera_mac=CAMERA)

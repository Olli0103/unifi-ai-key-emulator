"""Fail-closed interpretation of one AI Port smart-detection settings subset.

The observed Protect 7.2.105 interface informs field names. This independent
parser does not establish that Protect 7.3.60 accepts native smart events.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .aiport_ingest import IngressError, normalize_mac


_OBJECT_TYPES = frozenset({"person", "vehicle", "animal"})
_REGION_MAPS = frozenset({
    "zones", "secondLensZones", "lines", "loiterZones", "excludeZones",
    "intelligenceZones",
})
_OPTIONAL_ADVANCED = frozenset({
    "accessTrigger", "enablePTZAutoTracking", "depthEstimation",
    "reVerificationPolicy",
})
_ALLOWED = frozenset({
    "deviceID", "algoVersion", "enableSmartDetect", "eventStartMSec",
    "eventStopMSec", "region", "enableTamperDetection",
    "recognitionAccuracy",
}) | _REGION_MAPS | _OPTIONAL_ADVANCED


class SmartSettingsError(ValueError):
    """Fixed failure code; never includes camera identity or policy content."""


@dataclass(frozen=True)
class SmartPolicy:
    camera_mac: str
    enabled_types: frozenset[str]
    event_start_ms: int
    event_stop_ms: int

    def allows(self, kind: str) -> bool:
        return kind in self.enabled_types


def _timing(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 120_000:
        raise SmartSettingsError("invalid_smart_settings")
    return value


def parse_smart_settings(payload: object, *, camera_mac: str) -> SmartPolicy:
    """Accept only full-frame person, vehicle and animal settings.

    Any configured zone, line, exclusion, tamper, PTZ, access or second-stage
    policy is unsupported until the candidate can enforce it. The returned
    policy contains no raw nested payload and cannot enable native events by
    itself.
    """
    if (not isinstance(payload, dict) or not set(payload) <= _ALLOWED
            or not {"deviceID", "enableSmartDetect", "eventStartMSec",
                    "eventStopMSec"} <= set(payload)):
        raise SmartSettingsError("invalid_smart_settings")
    try:
        expected = normalize_mac(camera_mac)
        received = normalize_mac(payload["deviceID"])
    except IngressError as exc:
        raise SmartSettingsError("invalid_smart_settings") from exc
    if received != expected:
        raise SmartSettingsError("wrong_camera")
    if payload.get("algoVersion", "beta") != "beta":
        raise SmartSettingsError("invalid_smart_settings")
    region = payload.get("region")
    if region is not None and (not isinstance(region, str) or len(region) > 8):
        raise SmartSettingsError("invalid_smart_settings")
    requested = payload["enableSmartDetect"]
    if (not isinstance(requested, list) or len(requested) > len(_OBJECT_TYPES)
            or any(type(kind) is not str for kind in requested)
            or len(set(requested)) != len(requested)):
        raise SmartSettingsError("invalid_smart_settings")
    if not set(requested) <= _OBJECT_TYPES:
        raise SmartSettingsError("unsupported_smart_feature")
    start_ms = _timing(payload["eventStartMSec"])
    stop_ms = _timing(payload["eventStopMSec"])

    for name in _REGION_MAPS:
        value = payload.get(name, {})
        if not isinstance(value, dict):
            raise SmartSettingsError("invalid_smart_settings")
        if value:
            raise SmartSettingsError("unsupported_smart_feature")
    for name in _OPTIONAL_ADVANCED:
        value = payload.get(name)
        if value is not None and value is not False and not (
                type(value) is dict and not value):
            raise SmartSettingsError("unsupported_smart_feature")
    tamper = payload.get("enableTamperDetection")
    if tamper is not None and tamper is not False:
        raise SmartSettingsError("unsupported_smart_feature")

    accuracy = payload.get("recognitionAccuracy")
    if accuracy is not None:
        if not isinstance(accuracy, dict) or not set(accuracy) <= {
                "face", "licensePlate"}:
            raise SmartSettingsError("invalid_smart_settings")
        for value in accuracy.values():
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 100):
                raise SmartSettingsError("invalid_smart_settings")
    return SmartPolicy(expected, frozenset(requested), start_ms, stop_ms)

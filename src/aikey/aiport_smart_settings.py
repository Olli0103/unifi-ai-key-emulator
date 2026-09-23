"""Fail-closed interpretation of one AI Port smart-detection settings subset.

The observed Protect 7.2.105 interface informs field names. This independent
parser does not establish that Protect 7.3.60 accepts native smart events.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .aiport_ingest import IngressError, normalize_mac
from .aiport_zones import SmartZone, ZoneError, parse_smart_zones


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


def summarize_smart_request(payload: object, *, camera_mac: str) -> dict[str, int | bool]:
    """Return bounded protocol shape only, without camera or zone contents.

    This is for an expiring compatibility probe. It does not accept a policy.
    Unknown keys and nested values are never returned or persisted.
    """
    if not isinstance(payload, dict):
        return {"object": False}
    try:
        matches_camera = normalize_mac(payload.get("deviceID")) == normalize_mac(camera_mac)
    except IngressError:
        matches_camera = False
    requested = payload.get("enableSmartDetect")
    result: dict[str, int | bool] = {
        "object": True,
        "camera_matches": matches_camera,
        "known_fields": min(len(set(payload) & _ALLOWED), 64),
        "unknown_fields": min(len(set(payload) - _ALLOWED), 64),
        "enabled_list": isinstance(requested, list),
        "enabled_count": min(len(requested), 64) if isinstance(requested, list) else -1,
        "start_integer": type(payload.get("eventStartMSec")) is int,
        "stop_integer": type(payload.get("eventStopMSec")) is int,
        "beta_algorithm": payload.get("algoVersion") == "beta",
        "enabled_supported": (isinstance(requested, list)
                              and all(type(kind) is str and kind in _OBJECT_TYPES
                                      for kind in requested)),
        "timing_in_range": all(type(payload.get(name)) is int
                               and 0 <= payload[name] <= 120_000
                               for name in ("eventStartMSec", "eventStopMSec")),
        "region_compatible": (payload.get("region") is None
                              or isinstance(payload["region"], str)
                              and len(payload["region"]) <= 8),
        "reverification_disabled_exact": _disabled_reverification(
            payload.get("reVerificationPolicy")),
    }
    accuracy = payload.get("recognitionAccuracy")
    result["accuracy_compatible"] = (
        accuracy is None or isinstance(accuracy, dict)
        and set(accuracy) <= {"face", "licensePlate"}
        and all(value == "auto" or type(value) in (int, float)
                and math.isfinite(value) and 0 <= value <= 100
                for value in accuracy.values()))
    for name in sorted(_REGION_MAPS):
        value = payload.get(name)
        result[name + "_count"] = min(len(value), 64) if isinstance(value, dict) else -1
    for name in sorted(_OPTIONAL_ADVANCED | {"enableTamperDetection"}):
        result[name + "_configured"] = payload.get(name) not in (None, False, {})
    reverify = payload.get("reVerificationPolicy")
    result["reverification_object"] = isinstance(reverify, dict)
    result["reverification_known_count"] = (len(set(reverify) & _OBJECT_TYPES)
                                               if isinstance(reverify, dict) else -1)
    result["reverification_unknown_count"] = (len(set(reverify) - _OBJECT_TYPES)
                                                 if isinstance(reverify, dict) else -1)
    for kind in sorted(_OBJECT_TYPES):
        item = reverify.get(kind) if isinstance(reverify, dict) else None
        result["reverification_" + kind + "_object"] = isinstance(item, dict)
        result["reverification_" + kind + "_enabled"] = (
            isinstance(item, dict) and item.get("enable") is True)
        result["reverification_" + kind + "_enable_boolean"] = (
            isinstance(item, dict) and type(item.get("enable")) is bool)
    return result


def parse_motion_probe(payload: object, *, camera_mac: str) -> int:
    """Validate the exact old enhanced-motion envelope for a bounded probe.

    Returns only the number of zones. It does not apply motion policy and must
    never be used outside the expiring one-camera compatibility diagnostic.
    """
    if (not isinstance(payload, dict) or set(payload) != {
            "algoVersion", "deviceID", "enable", "eventMaxDurationMSec",
            "bgmodel", "lingerEventStartMSec", "lingerEventStopMSec", "zones"}
            or payload["algoVersion"] != "beta"
            or payload["bgmodel"] != "default"
            or type(payload["enable"]) is not bool):
        raise SmartSettingsError("invalid_motion_probe")
    try:
        if normalize_mac(payload["deviceID"]) != normalize_mac(camera_mac):
            raise SmartSettingsError("wrong_camera")
    except IngressError as exc:
        raise SmartSettingsError("invalid_motion_probe") from exc
    duration = payload["eventMaxDurationMSec"]
    if type(duration) is not int or not 0 < duration <= 86_400_000:
        raise SmartSettingsError("invalid_motion_probe")
    _timing(payload["lingerEventStartMSec"])
    _timing(payload["lingerEventStopMSec"])
    zones = payload["zones"]
    if not isinstance(zones, dict) or len(zones) > 32:
        raise SmartSettingsError("invalid_motion_probe")
    return len(zones)


@dataclass(frozen=True)
class SmartPolicy:
    camera_mac: str
    enabled_types: frozenset[str]
    event_start_ms: int
    event_stop_ms: int
    reverification_ceilings: tuple[tuple[str, float], ...]
    smart_zones: tuple[SmartZone, ...]
    zones_configured: bool

    def allows(self, kind: str) -> bool:
        return kind in self.enabled_types

    def allows_score(self, kind: str, score: float) -> bool:
        """Suppress uncertain observations needing a second stage.

        A high score outside Protect's reverification range needs no second
        stage. This deliberately drops the uncertain range; it does not claim
        to perform reverification or reproduce Protect's AI Key behavior.
        """
        ceiling = dict(self.reverification_ceilings).get(kind)
        return (self.allows(kind) and type(score) in (int, float)
                and math.isfinite(score) and 0 <= score <= 1
                and (ceiling is None or score > ceiling))

    def zone_ids(self, kind: str,
                 box: tuple[float, float, float, float]) -> tuple[int, ...] | None:
        """Return matching zone IDs, or deny the box if zones are configured."""
        if not self.allows(kind):
            return None
        if not self.zones_configured:
            return ()
        matches = tuple(zone.zone_id for zone in self.smart_zones
                        if kind in zone.object_types and zone.contains_box(box))
        return matches or None

    @property
    def person_reverification_ceiling(self) -> float | None:
        return dict(self.reverification_ceilings).get("person")

    def allows_person_score(self, score: float) -> bool:
        return self.allows_score("person", score)

    def person_zone_ids(self, box: tuple[float, float, float, float]) -> tuple[int, ...] | None:
        return self.zone_ids("person", box)


def _timing(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 120_000:
        raise SmartSettingsError("invalid_smart_settings")
    return value


def _disabled_reverification(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
            "person", "vehicle", "animal"}:
        return False
    return all(isinstance(value[kind], dict)
               and set(value[kind]) == {"enable"}
               and value[kind]["enable"] is False
               for kind in ("person", "vehicle", "animal"))


def _reverification_ceilings(value: object, requested: list[str]
                             ) -> tuple[tuple[str, float], ...]:
    """Validate the old per-class policy and derive conservative event gates.

    Enabled reverification drops uncertain observations for each requested
    class; it does not perform a second inference pass.
    """
    if not isinstance(value, dict) or set(value) != _OBJECT_TYPES:
        raise SmartSettingsError("unsupported_smart_feature")
    ceilings = []
    for kind, item in value.items():
        if not isinstance(item, dict) or type(item.get("enable")) is not bool:
            raise SmartSettingsError("unsupported_smart_feature")
        if not item["enable"]:
            if set(item) != {"enable"}:
                raise SmartSettingsError("unsupported_smart_feature")
            continue
        if set(item) != {"enable", "mode", "minPresenceProbability",
                         "maxPresenceProbability"} or item["mode"] != "custom":
            raise SmartSettingsError("unsupported_smart_feature")
        minimum = item["minPresenceProbability"]
        maximum = item["maxPresenceProbability"]
        if (type(minimum) is not int or type(maximum) is not int
                or not 0 <= minimum <= maximum <= 100):
            raise SmartSettingsError("unsupported_smart_feature")
        if kind in requested:
            ceilings.append((kind, maximum / 100))
    return tuple(sorted(ceilings))


def parse_smart_settings(payload: object, *, camera_mac: str) -> SmartPolicy:
    """Accept bounded primary zones or full-frame object settings.

    Secondary-lens zones, lines, exclusions, tamper, PTZ and access triggers
    remain unsupported. Enabled reverification suppresses uncertain
    observations; it is not a second-stage inference implementation. The
    returned policy cannot enable native events by itself.
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

    raw_zones = payload.get("zones", {})
    try:
        smart_zones = parse_smart_zones(raw_zones)
    except ZoneError as exc:
        raise SmartSettingsError(str(exc)) from exc
    # Protect 7.3.60 can send an empty top-level list for a legacy camera
    # while a Detection Zone carries the requested class. Only derive a
    # single supported class from validated primary-lens zones; never turn
    # an empty policy with no usable zone into full-frame detection.
    if not requested and smart_zones:
        zone_types = set().union(*(zone.object_types for zone in smart_zones))
        if len(zone_types) == 1:
            requested = list(zone_types)
    for name in _REGION_MAPS - {"zones"}:
        value = payload.get(name, {})
        if not isinstance(value, dict):
            raise SmartSettingsError("invalid_smart_settings")
        if value:
            raise SmartSettingsError("unsupported_smart_feature")
    for name in _OPTIONAL_ADVANCED - {"reVerificationPolicy"}:
        value = payload.get(name)
        if value is not None and value is not False and not (
                type(value) is dict and not value):
            raise SmartSettingsError("unsupported_smart_feature")
    reverify = payload.get("reVerificationPolicy")
    ceilings = (_reverification_ceilings(reverify, requested)
                if reverify not in (None, {}) else ())
    tamper = payload.get("enableTamperDetection")
    if tamper is not None and tamper is not False:
        raise SmartSettingsError("unsupported_smart_feature")

    accuracy = payload.get("recognitionAccuracy")
    if accuracy is not None:
        if not isinstance(accuracy, dict) or not set(accuracy) <= {
                "face", "licensePlate"}:
            raise SmartSettingsError("invalid_smart_settings")
        for value in accuracy.values():
            # Protect's deprecated precision setting is a read-only string
            # whose current value is "auto". It does not request a face or
            # plate recognizer from this candidate.
            if value == "auto":
                continue
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 100):
                raise SmartSettingsError("invalid_smart_settings")
    return SmartPolicy(expected, frozenset(requested), start_ms, stop_ms,
                       ceilings, smart_zones, bool(raw_zones))

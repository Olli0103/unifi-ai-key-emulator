"""Build one bounded native smart-event candidate from a local track.

The wire shape is inferred from the controller's interface and still needs a
live original-camera timeline check. No frames or recognition data are kept.
"""

from __future__ import annotations

import math

from .aiport_ingest import normalize_mac
from .aiport_tracking import TrackChange


class SmartEventError(ValueError):
    """Fixed validation failure without camera or object details."""


def smart_event_payload(camera_mac: str, change: TrackChange, *,
                        edge: str, clock_wall_ms: int,
                        zone_ids: tuple[int, ...] = (),
                        first_shown_ms: int | None = None) -> dict:
    """Encode one bounded object track edge, without recognition claims."""
    if (not isinstance(change, TrackChange)
            or change.kind not in {"person", "vehicle", "animal", "package"}
            or edge not in {"enter", "moving", "leave", "packageDetected"}
            or (edge == "packageDetected") != (change.kind == "package")
            or type(clock_wall_ms) is not int or clock_wall_ms <= 0
            or type(change.track_id) is not int or change.track_id <= 0
            or (first_shown_ms is not None
                and (type(first_shown_ms) is not int
                     or not 0 < first_shown_ms <= clock_wall_ms))
            or not math.isfinite(change.score) or not 0 <= change.score <= 1
            or not isinstance(zone_ids, tuple) or len(zone_ids) > 32
            or any(type(zone_id) is not int or not 1 <= zone_id <= 4_294_967_295
                   for zone_id in zone_ids)
            or len(set(zone_ids)) != len(zone_ids)):
        raise SmartEventError("invalid_smart_event")
    try:
        device_id = normalize_mac(camera_mac)
    except ValueError as exc:
        raise SmartEventError("invalid_smart_event") from exc
    payload = {"deviceID": device_id, "edgeType": edge,
               "clockWall": clock_wall_ms,
               "displayTimeoutMSec": 1000,
               # The local object score supplies a bounded zone level. Its
               # exact relationship to stock-camera zone levels is unverified.
               # A moving track updates its descriptor without claiming a new
               # zone transition. Zone status has only enter/leave edges.
               # Protect saves a package as a one-shot event; its zone status
               # only carries the score, so report it as a zone entry.
               "zonesStatus": {str(zone_id): {
                   "status": "enter" if edge == "packageDetected" else edge,
                   "level": round(change.score * 100)}
                   for zone_id in zone_ids if edge != "moving"},
               "trackerIDAttrMap": {}}
    x1, y1, x2, y2 = change.box
    if not all(math.isfinite(v) for v in change.box) or not (
            0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise SmartEventError("invalid_smart_event")
    descriptor = {
        "trackerID": change.track_id,
        # The controller interprets a vehicle name as a license plate.
        # An object detector cannot supply one.
        "name": "" if change.kind == "vehicle" else change.kind,
        "confidenceLevel": round(change.score * 100),
        "coord": [round(x1 * 1000), round(y1 * 1000),
                  round((x2 - x1) * 1000), round((y2 - y1) * 1000)],
        "objectType": change.kind, "zones": list(zone_ids), "lines": [],
        "stationary": False, "attributes": {}, "coord3d": [],
    }
    if first_shown_ms is not None:
        descriptor["firstShownTimeMs"] = first_shown_ms
    payload.update({
        "objectTypes": [change.kind],
        "descriptors": [descriptor],
    })
    if edge == "leave":
        # The final per-track class is carried separately from descriptors.
        # Keep the association limited to the observed class and zone; no
        # recognition or image metadata can be inferred from a box track.
        payload["trackerIDAttrMap"] = {
            str(change.track_id): {"objectType": change.kind,
                                   "zone": list(zone_ids)}
        }
    return payload

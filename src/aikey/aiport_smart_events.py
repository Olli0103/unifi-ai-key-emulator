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
                        edge: str, clock_wall_ms: int) -> dict:
    """Encode one person enter or leave, without claiming recognition support."""
    if (not isinstance(change, TrackChange) or change.kind != "person"
            or edge not in {"enter", "leave"}
            or type(clock_wall_ms) is not int or clock_wall_ms <= 0
            or type(change.track_id) is not int or change.track_id <= 0
            or not math.isfinite(change.score) or not 0 <= change.score <= 1):
        raise SmartEventError("invalid_smart_event")
    try:
        device_id = normalize_mac(camera_mac)
    except ValueError as exc:
        raise SmartEventError("invalid_smart_event") from exc
    payload = {"deviceID": device_id, "edgeType": edge,
               "clockWall": clock_wall_ms, "zonesStatus": {},
               "trackerIDAttrMap": {}}
    if edge == "leave":
        return payload
    x1, y1, x2, y2 = change.box
    if not all(math.isfinite(v) for v in change.box) or not (
            0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise SmartEventError("invalid_smart_event")
    payload.update({
        "displayTimeoutMSec": 1000,
        "objectTypes": ["person"],
        "descriptors": [{
            "trackerID": change.track_id,
            "name": "person",
            "confidenceLevel": round(change.score * 100),
            "coord": [round(x1 * 1000), round(y1 * 1000),
                      round((x2 - x1) * 1000), round((y2 - y1) * 1000)],
            "objectType": "person", "zones": [], "lines": [],
            "stationary": False, "attributes": {}, "coord3d": [],
        }],
    })
    return payload

"""Short-lived fingerprints for matching a wire send to native read-back.

This does not assert controller acceptance, timeline persistence or quality.
Only random-free correlation digests are retained, never media, raw camera
identifiers, object labels, coordinates, descriptions or identity templates.
Digests are linkable diagnostic metadata, not anonymization or authentication.
"""

from collections import OrderedDict
import hashlib
import json
import time

from .aiport_ingest import normalize_mac

SCHEMA = "local-aiport-native-snapshot/1"
SCOPE = "wire_sent_only_native_readback_required"
CAPACITY = 64
RETENTION_SECONDS = 900
KINDS = frozenset({"person", "vehicle", "animal", "package"})


def fingerprint(camera_mac, kind, tracker_id, clock_best_wall):
    """Same canonical tuple is available in native detectedThumbnails.

    JavaScript peers must use safe positive integers and JSON.stringify on
    [uppercase separator-free MAC, kind, trackerId, clockBestWall], prefixed
    with SCHEMA and one NUL byte before SHA-256. No wall-clock rounding.
    """
    camera = normalize_mac(camera_mac)
    if (not isinstance(kind, str) or kind not in KINDS
            or any(type(v) is not int or not 1 <= v <= 2**53 - 1
                   for v in (tracker_id, clock_best_wall))):
        raise ValueError("invalid_native_correlation")
    raw = json.dumps([camera, kind, tracker_id, clock_best_wall], separators=(",", ":"))
    return hashlib.sha256((SCHEMA + "\0" + raw).encode("ascii")).hexdigest()


class SentSnapshots:
    """One process, at most 64 distinct tuples and 15 minutes since first send.

    Repeated sends do not extend retention or change the canonical tuple.
    Restart loses this evidence. An empty set is not proof of no detections.
    """

    def __init__(self, cameras, *, clock=time.monotonic):
        self.cameras = frozenset(normalize_mac(c) for c in cameras)
        if len(self.cameras) > 5:
            raise ValueError("invalid_native_correlation")
        self.clock = clock
        self.rows = OrderedDict()

    def _prune(self, now):
        self.rows = OrderedDict((key, until) for key, until in self.rows.items() if now < until)

    def record(self, payload):
        """Call only after send_bytes completed; malformed diagnostics are ignored."""
        if not isinstance(payload, dict) or payload.get("edgeType") != "leave":
            return
        try:
            camera = normalize_mac(payload.get("deviceID"))
        except ValueError:
            return
        snapshots = payload.get("smartDetectSnapshots")
        if camera not in self.cameras or not isinstance(snapshots, list) or len(snapshots) > 32:
            return
        now = self.clock()
        self._prune(now)
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            try:
                digest = fingerprint(camera, snapshot.get("smartDetectSnapshotType"),
                                     snapshot.get("trackerID"), snapshot.get("clockBestWall"))
            except ValueError:
                continue
            if digest not in self.rows:
                self.rows[digest] = now + RETENTION_SECONDS
                while len(self.rows) > CAPACITY:
                    self.rows.popitem(last=False)

    def view(self):
        self._prune(self.clock())
        return {"schema": SCHEMA, "scope": SCOPE, "capacity": CAPACITY,
                "retention_seconds": RETENTION_SECONDS, "fingerprints": list(self.rows)}

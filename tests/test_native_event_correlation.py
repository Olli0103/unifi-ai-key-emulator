"""Wire completion and native persistence are separate, bounded observations."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from aikey.admin_runtime import _normalize
from aikey.aiport_candidate import CandidateService
from aikey.native_event_correlation import CAPACITY, RETENTION_SECONDS, SentSnapshots, fingerprint
from test_aiport_candidate import fixture_state

CAMERA = "2A1122334455"
OTHER = "2A1122334456"
WALL = 1793360000000


def event(camera=CAMERA, tracker=7, wall=WALL):
    return {
        "deviceID": camera,
        "edgeType": "leave",
        "clockWall": wall + 1000,
        "smartDetectSnapshots": [
            {
                "smartDetectSnapshotType": "person",
                "trackerID": tracker,
                "clockBestWall": wall,
                "smartDetectSnapshot": "fictional-private-filename.jpg",
                "private": "fictional-identity-or-text-never-retained",
            }
        ],
    }


def test_canonical_native_tuple_does_not_depend_on_private_snapshot_fields():
    raw = b'local-aiport-native-snapshot/1\0["2A1122334455","person",7,1793360000000]'
    expected = hashlib.sha256(raw).hexdigest()
    assert fingerprint("2a:11:22:33:44:55", "person", 7, WALL) == expected
    assert fingerprint(OTHER, "person", 7, WALL) != expected
    for changed in [("vehicle", 7, WALL), ("person", 8, WALL), ("person", 7, WALL + 1)]:
        assert fingerprint(CAMERA, *changed) != expected
    ledger = SentSnapshots([CAMERA])
    data = event()
    ledger.record(data)
    data["smartDetectSnapshots"][0]["trackerID"] = 999
    report = ledger.view()
    assert report["fingerprints"] == [expected]
    assert report["scope"] == "wire_sent_only_native_readback_required"
    assert all(v not in json.dumps(report) for v in (CAMERA, "fictional", str(WALL), "person"))


def test_capacity_expiry_restart_and_repeated_send_do_not_claim_complete_history():
    now = [0.0]
    ledger = SentSnapshots([CAMERA], clock=lambda: now[0])
    ledger.record(event())
    now[0] = RETENTION_SECONDS - 1
    ledger.record(event())
    assert len(ledger.view()["fingerprints"]) == 1
    now[0] = RETENTION_SECONDS
    assert ledger.view()["fingerprints"] == []
    for i in range(CAPACITY + 20):
        ledger.record(event(tracker=i + 1))
    result = ledger.view()
    assert len(result["fingerprints"]) == CAPACITY
    assert fingerprint(CAMERA, "person", 1, WALL) not in result["fingerprints"]
    assert SentSnapshots([CAMERA]).view()["fingerprints"] == []
    result["fingerprints"].clear()
    assert len(ledger.view()["fingerprints"]) == CAPACITY


@pytest.mark.parametrize(
    "damage",
    [
        "other_camera",
        "moving",
        "oversized",
        "bad_kind",
        "bool_tracker",
        "unsafe_integer",
        "bad_clock",
        "missing",
        "not_dict",
    ],
)
def test_unsupported_or_malformed_metadata_is_not_retained(damage):
    ledger = SentSnapshots([CAMERA])
    data = event()
    snapshot = data["smartDetectSnapshots"][0]
    if damage == "other_camera":
        data["deviceID"] = OTHER
    elif damage == "moving":
        data["edgeType"] = "moving"
    elif damage == "oversized":
        data["smartDetectSnapshots"] *= 33
    elif damage == "bad_kind":
        snapshot["smartDetectSnapshotType"] = ["person"]
    elif damage == "bool_tracker":
        snapshot["trackerID"] = True
    elif damage == "unsafe_integer":
        snapshot["trackerID"] = 2**53
    elif damage == "bad_clock":
        snapshot["clockBestWall"] = float("inf")
    elif damage == "missing":
        del snapshot["trackerID"]
    elif damage == "not_dict":
        data = []
    ledger.record(data)
    assert ledger.view()["fingerprints"] == []


async def test_only_completed_smart_wire_sends_are_recorded_without_changing_wire(tmp_path):
    from types import SimpleNamespace

    config = fixture_state(tmp_path)
    service = CandidateService(config, tmp_path)
    service._sent_snapshots = SentSnapshots([CAMERA])
    sent = []

    async def sink(raw):
        sent.append(raw)

    ws = SimpleNamespace(send_bytes=sink)
    original = event()
    before = deepcopy(original)
    await service._send_control_event(ws, "EventSmartDetect", original)
    assert original == before
    wire = json.loads(sent[0])
    assert wire["payload"] == before and "native_event_correlation" not in wire
    assert service._sent_snapshots.view()["fingerprints"] == [
        fingerprint(CAMERA, "person", 7, WALL)
    ]
    await service._send_control_event(ws, "EventSmartMotion", event(tracker=8))
    assert len(service._sent_snapshots.view()["fingerprints"]) == 1

    async def broken(raw):
        raise OSError("fictional-send-failed")

    with pytest.raises(OSError):
        await service._send_control_event(
            SimpleNamespace(send_bytes=broken), "EventSmartDetect", event(tracker=9)
        )
    assert len(service._sent_snapshots.view()["fingerprints"]) == 1

    async def cancelled(raw):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await service._send_control_event(
            SimpleNamespace(send_bytes=cancelled), "EventSmartDetect", event(tracker=10)
        )
    assert len(service._sent_snapshots.view()["fingerprints"]) == 1


@pytest.mark.parametrize(
    "damage", [None, "extra", "duplicate", "over_capacity", "scope", "hash", "bool_capacity"]
)
def test_authenticated_runtime_view_keeps_only_strict_correlation_metadata(damage):
    ledger = SentSnapshots([CAMERA])
    ledger.record(event())
    view = ledger.view()
    value = {"service": "aiport-candidate", "adopted": True, "native_event_correlation": view}
    if damage == "extra":
        view["private"] = "must-not-leak"
    elif damage == "duplicate":
        view["fingerprints"] *= 2
    elif damage == "over_capacity":
        view["fingerprints"] = [f"{i:064x}" for i in range(CAPACITY + 1)]
    elif damage == "scope":
        view["scope"] = "native_verified"
    elif damage == "hash":
        view["fingerprints"] = ["must-not-leak"]
    elif damage == "bool_capacity":
        view["capacity"] = True
    if damage:
        with pytest.raises(ValueError, match="Invalid native event correlation"):
            _normalize("aiport", value)
    else:
        result = _normalize("aiport", value)
        assert result["native_event_correlation"] == view
        result["native_event_correlation"]["fingerprints"].clear()
        assert len(view["fingerprints"]) == 1

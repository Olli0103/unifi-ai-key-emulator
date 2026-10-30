"""Bounded metadata diagnostics cannot become admission or replay authority."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from aikey import device as d
from aikey.protocol import decode_message
from aikey.worker import WorkerError
from test_basic_descriptions import command, device_config, wire


CAMERA = "1234567890abcdef12345678"
EVENT = "private-event-canary"


def body(event=EVENT):
    value = command(event)["payload"]
    value.update(
        camera=CAMERA,
        reqUrl="https://private.invalid/token=secret-canary",
        resUrl="/private-callback-canary",
        faceMeta={"private-face": [1, 2, 3]},
    )
    return value


def config():
    value = device_config()
    value["worker"]["test_scope"]["camera_id"] = CAMERA
    return value


def ledger(clock):
    return d._RecentRecognizeRejections(
        reasons={"video_interval", "payload_fields", "unclassified_worker_error"}, clock=clock
    )


def test_only_safe_metadata_is_retained_and_the_native_fingerprint_is_reproducible():
    samples = ledger(lambda: 100.0)
    original = body()
    frozen = deepcopy(original)
    samples.record(original, "video_interval")
    view = samples.view()
    assert view["schema"] == "local-aikey-native-rejection/1"
    assert view["scope"] == "worker_rejection_only_native_outcome_unknown"
    assert view["capacity"] == 64 and view["retention_seconds"] == 900
    (row,) = view["records"]
    raw = "local-aikey-native-rejection/1\0" + json.dumps([CAMERA, EVENT], separators=(",", ":"))
    assert row == {
        "event_fingerprint": hashlib.sha256(raw.encode("ascii")).hexdigest(),
        "reason": "video_interval",
        "ram_type": "video",
        "post_vlm": True,
        "metadata_mask": 2,
        "source_span_ms": 10000,
        "key_moment_count": 2,
    }
    for value in [
        CAMERA,
        EVENT,
        "private.invalid",
        "secret-canary",
        "private-face",
        "private-callback-canary",
    ]:
        assert value not in json.dumps(view)
        assert value not in repr(samples.__dict__)
    original["faceMeta"]["private-face"].append(99)
    view["records"][0]["reason"] = "modified"
    assert samples.view()["records"][0]["reason"] == "video_interval"
    assert frozen != original


def test_duplicate_does_not_extend_first_seen_retention():
    now = [0.0]
    samples = ledger(lambda: now[0])
    samples.record(body(), "video_interval")
    now[0] = 899.0
    samples.record(body(), "video_interval")
    assert len(samples.view()["records"]) == 1
    now[0] = 900.0
    assert samples.view()["records"] == []


def test_capacity_and_payload_are_bounded_without_losing_distinct_reasons():
    samples = ledger(lambda: 100.0)
    for i in range(150):
        samples.record(body(f"synthetic-{i}"), "video_interval")
    view = samples.view()
    assert len(view["records"]) == 64 and len(json.dumps(view)) < 32768
    samples.record(body("synthetic-149"), "payload_fields")
    assert len(samples.view()["records"]) == 64
    assert samples.view()["records"][-1]["reason"] == "payload_fields"
    assert ledger(lambda: 100.0).view()["records"] == []  # No restart persistence.


@pytest.mark.parametrize(
    "field,value",
    [
        ("camera", None),
        ("camera", True),
        ("camera", []),
        ("camera", "not-a-native-id"),
        ("event", None),
        ("event", {}),
        ("event", ""),
        ("event", "a" * 129),
        ("event", "line\nprivate-canary"),
    ],
)
def test_invalid_identifiers_are_not_captured(field, value):
    samples = ledger(lambda: 100.0)
    payload = body()
    payload[field] = value
    samples.record(payload, "video_interval")
    assert samples.view()["records"] == []


@pytest.mark.parametrize("value", [None, [], "raw-private-message", "payload_fields;canary"])
def test_unknown_reason_cannot_become_a_stored_label(value):
    samples = ledger(lambda: 100.0)
    samples.record(body(), value)
    assert samples.view()["records"] == []


@pytest.mark.parametrize("clock_value", [float("nan"), float("inf"), -1, None, True, "100"])
def test_invalid_clock_cannot_capture_or_retain_stale_samples(clock_value):
    now = [100.0]
    samples = ledger(lambda: now[0])
    samples.record(body(), "video_interval")
    now[0] = clock_value
    samples.record(body("other"), "video_interval")
    assert samples.view()["records"] == []


def test_backwards_or_raising_clock_drops_evidence_not_admission():
    now = [100.0]
    samples = ledger(lambda: now[0])
    samples.record(body(), "video_interval")
    now[0] = 99.0
    assert samples.view()["records"] == []

    def unavailable():
        raise RuntimeError("private-clock-canary")

    samples.clock = unavailable
    samples.record(body(), "video_interval")
    assert samples.view()["records"] == []


def test_untrusted_shapes_do_not_retain_values_or_exceed_metadata_bounds():
    samples = ledger(lambda: 100.0)
    payload = body()
    payload.update(
        ramType="private-type-canary",
        postVLM="private-flag-canary",
        start=True,
        end=2**70,
        keyMoments=["private-moment-canary"] * 10000,
    )
    samples.record(payload, "video_interval")
    (row,) = samples.view()["records"]
    assert row["ram_type"] == "other" and row["post_vlm"] is None
    assert row["source_span_ms"] is None and row["key_moment_count"] == 129
    assert "private-" not in json.dumps(samples.view())


@pytest.mark.parametrize(
    "error,reason",
    [
        (
            WorkerError("recognizeKeyFrames video exceeds configured duration bound"),
            "video_interval",
        ),
        (WorkerError("private-token-canary"), "unclassified_worker_error"),
    ],
)
async def test_only_the_existing_worker_refusal_path_records_one_tuple(tmp_path, error, reason):
    calls = []

    async def reject(value):
        calls.append(value)
        raise error

    service = d.DeviceService(config(), tmp_path, reject)
    message = wire("recognizeKeyFrames", body())
    first = await service.handle_message(message)
    assert decode_message(first).header["errorCode"] == 5
    assert await service.handle_message(message) == first
    assert len(calls) == 1 and service._active_admissions == 0
    rows = service.status["recognize_rejection_samples"]["records"]
    assert len(rows) == 1 and rows[0]["reason"] == reason
    assert service.status["recognize_key_frames"]["phase_counts"]["worker_rejected"] == 1
    assert list(tmp_path.iterdir()) == []
    assert "private-token-canary" not in json.dumps(service.status)


@pytest.mark.parametrize(
    "outcome,code",
    [("accepted", 0), ("invalid", 22), ("timeout", 110), ("exception", 22), ("wrong_camera", 95)],
)
async def test_success_uncertainty_and_policy_denial_are_not_worker_rejection_samples(
    tmp_path, outcome, code
):
    calls = []

    async def handle(value):
        calls.append(value)
        if outcome == "timeout":
            raise asyncio.TimeoutError
        if outcome == "exception":
            raise ValueError("private-error-canary")
        return None if outcome == "invalid" else {"accepted": True}

    service = d.DeviceService(config(), tmp_path, handle)
    payload = body()
    if outcome == "wrong_camera":
        payload["camera"] = "0" * 24
    result = decode_message(await service.handle_message(wire("recognizeKeyFrames", payload)))
    assert result.header["errorCode"] == code and service._active_admissions == 0
    assert service.status["recognize_rejection_samples"]["records"] == []
    assert len(calls) == (0 if outcome == "wrong_camera" else 1)


async def test_diagnostic_fault_does_not_replace_the_original_refusal_or_reply(tmp_path):
    async def reject(value):
        raise WorkerError("Unsupported recognizeKeyFrames payload fields")

    service = d.DeviceService(config(), tmp_path, reject)

    def broken(*args):
        raise RuntimeError("private-diagnostic-canary")

    service._recent_recognize_rejections.record = broken
    result = decode_message(await service.handle_message(wire("recognizeKeyFrames", body())))
    assert result.header["errorCode"] == 5 and result.header["error"] == "Command failed"
    assert service._active_admissions == 0 and not service._pending
    assert "private-diagnostic-canary" not in json.dumps(service.status)


async def test_cancellation_is_not_a_worker_refusal_and_keeps_cleanup(tmp_path):
    started = asyncio.Event()

    async def wait_for_cancel(value):
        started.set()
        await asyncio.Future()

    service = d.DeviceService(config(), tmp_path, wait_for_cancel)
    task = asyncio.create_task(service.handle_message(wire("recognizeKeyFrames", body())))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service._active_admissions == 0 and not service._pending and not service._completed
    assert service.status["recognize_rejection_samples"]["records"] == []
    assert service.status["recognize_key_frames"]["phase_counts"]["admission_cancelled"] == 1

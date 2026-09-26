"""Sanitized control diagnostics. No network or private deployment inputs."""

import asyncio
import json

import pytest

from aikey.device import CommandFailure, DeviceService
from aikey.protocol import decode_message
from aikey.worker import WorkerError
from test_basic_descriptions import command, device_config, wire


async def test_matching_worker_failure_remains_visible_after_other_camera_rejection(tmp_path):
    async def reject(body):
        raise WorkerError("Unsupported recognizeKeyFrames payload fields")
    device = DeviceService(device_config(), tmp_path, reject)
    body = command()["payload"]
    first = wire("recognizeKeyFrames", body)
    assert decode_message(await device.handle_message(first)).header["errorCode"] == 5
    await device.handle_message(first)  # Cached duplicate must not inflate counters.
    wrong_camera = {**body, "camera": "private-other-camera-id"}
    assert decode_message(await device.handle_message(
        wire("recognizeKeyFrames", wrong_camera, "other"))).header["errorCode"] == 95
    status = device.status
    controls = status["control_commands"]["recognizeKeyFrames"]
    assert controls["count"] == 2 and controls["last_result_code"] == 95
    assert controls["result_code_counts"]["5"] == controls["result_code_counts"]["95"] == 1
    detail = status["recognize_key_frames"]
    assert detail["camera_shape_counts"]["string"] == 2
    assert detail["camera_match_counts"] == {"matches": 1, "different": 1, "not_comparable": 0}
    assert detail["matching_camera_result_code_counts"]["5"] == 1
    assert detail["matching_camera_result_code_counts"]["95"] == 0
    assert detail["phase_counts"]["worker_admission"] == 1
    assert detail["phase_counts"]["worker_rejected"] == 1
    assert detail["phase_counts"]["camera_mismatch"] == 1
    assert detail["worker_rejection_counts"]["payload_fields"] == 1
    for private_value in (body["camera"], body["event"], body["reqUrl"], "private-other-camera-id"):
        assert private_value not in json.dumps(status)


async def test_alternate_camera_id_and_ram_enum_are_observed_without_changing_scope(tmp_path):
    calls = []
    async def admit(body):
        calls.append(body)
        return {"accepted": True}
    device = DeviceService(device_config(), tmp_path, admit)
    reply = decode_message(await device.handle_message(wire("recognizeKeyFrames", {
        "cameraId": "camera-fixture", "ramType": "multipleImages",
        "token": "secret-never-in-health", "reqUrl": "https://private.invalid/value"})))
    assert reply.header["errorCode"] == 95 and calls == []
    detail = device.status["recognize_key_frames"]
    assert detail["camera_shape_counts"]["missing"] == 1
    assert detail["cameraId_shape_counts"]["string"] == 1
    assert detail["cameraId_match_counts"]["matches"] == 1
    assert detail["matching_cameraId_result_code_counts"]["95"] == 1
    assert detail["matching_camera_result_code_counts"]["95"] == 0
    assert detail["ram_type_counts"]["multipleImages"] == 1
    assert "secret-never-in-health" not in json.dumps(device.status)
    assert "private.invalid" not in json.dumps(device.status)


@pytest.mark.parametrize("value,shape", [
    (None, "null"), (True, "boolean"), (123, "number"), (1.5, "number"),
    ([], "array"), ({"secret": "value"}, "object"), ("unmatched-value", "string"),
])
async def test_camera_shape_categories_never_retain_values(tmp_path, value, shape):
    async def admit(body):
        pytest.fail("Wrongly shaped or unmatched camera must not reach admission")
    device = DeviceService(device_config(), tmp_path, admit)
    await device.handle_message(wire("recognizeKeyFrames", {
        "camera": value, "cameraId": value, "ramType": {"secret": "value"}}))
    detail = device.status["recognize_key_frames"]
    assert detail["camera_shape_counts"][shape] == 1
    assert detail["cameraId_shape_counts"][shape] == 1
    assert detail["ram_type_counts"]["invalid_type"] == 1
    assert "secret" not in json.dumps(device.status)
    assert "unmatched-value" not in json.dumps(device.status)


@pytest.mark.parametrize("error,phase,reason,code", [
    (WorkerError("recognizeKeyFrames video must span at most 10 seconds"), "worker_rejected", "video_interval", 5),
    (WorkerError("private-token=canary; https://private.invalid/value"), "worker_rejected", "unclassified_worker_error", 5),
    (ValueError("private-token=canary"), "admission_exception", None, 22),
    (asyncio.TimeoutError("private-token=canary"), "admission_timeout", None, 110),
])
async def test_only_exact_allowlisted_worker_messages_become_safe_labels(tmp_path, error, phase, reason, code):
    async def reject(body):
        raise error
    device = DeviceService(device_config(), tmp_path, reject)
    result = decode_message(await device.handle_message(wire("recognizeKeyFrames", command()["payload"])))
    assert result.header["errorCode"] == code
    detail = device.status["recognize_key_frames"]
    assert detail["phase_counts"][phase] == 1
    assert detail["matching_camera_result_code_counts"][str(code)] == 1
    if reason:
        assert detail["worker_rejection_counts"][reason] == 1
    assert "private-token" not in json.dumps(device.status)
    assert "private.invalid" not in json.dumps(device.status)
    assert "private-token" not in str(result.header)


async def test_success_invalid_result_and_disabled_scope_have_distinct_phases(tmp_path):
    async def admit(body):
        return {"accepted": True}
    device = DeviceService(device_config(), tmp_path, admit)
    await device.handle_message(wire("recognizeKeyFrames", command()["payload"]))
    assert device.status["recognize_key_frames"]["phase_counts"]["admitted"] == 1
    async def invalid(body):
        return None
    device.job_handler = invalid
    reply = decode_message(await device.handle_message(wire("recognizeKeyFrames", command()["payload"], "two")))
    assert reply.header["errorCode"] == 22
    assert device.status["recognize_key_frames"]["phase_counts"]["invalid_admission_result"] == 1
    device.config["worker"].pop("test_scope")
    await device.handle_message(wire("recognizeKeyFrames", command()["payload"], "three"))
    assert device.status["recognize_key_frames"]["phase_counts"]["scope_disabled"] == 1


async def test_unknown_names_codes_and_ram_values_cannot_expand_diagnostics(tmp_path):
    async def reject(body):
        raise CommandFailure(999999, "fixed fixture failure")
    device = DeviceService(device_config(), tmp_path, reject)
    original_keys = set(device.status["control_commands"])
    for index in range(5):
        await device.handle_message(wire("secret-command-" + str(index), {}, "unknown-" + str(index)))
    body = {**command()["payload"], "ramType": "secret-ram-type"}
    await device.handle_message(wire("recognizeKeyFrames", body))
    diagnostics = device.status
    assert set(diagnostics["control_commands"]) == original_keys
    assert diagnostics["control_commands"]["unknown"]["count"] == 5
    known = diagnostics["control_commands"]["recognizeKeyFrames"]
    assert known["last_result_code"] is None and known["result_code_counts"]["other"] == 1
    assert diagnostics["recognize_key_frames"]["ram_type_counts"]["other_string"] == 1
    assert "secret-command" not in json.dumps(diagnostics)
    assert "secret-ram-type" not in json.dumps(diagnostics)
    assert "999999" not in json.dumps(diagnostics)
    device._control_diagnostics["unknown"]["count"] = 2 ** 31 - 1
    await device.handle_message(wire("another-secret-command", {}, "last"))
    assert device.status["control_commands"]["unknown"]["count"] == 2 ** 31 - 1


async def test_native_metadata_and_video_frame_shapes_are_bucketed_without_values(tmp_path):
    async def reject(body):
        raise WorkerError("Unsupported recognizeKeyFrames payload fields")
    device = DeviceService(device_config(), tmp_path, reject)
    body = {**command()["payload"], "start": 1000000, "end": 1028302,
            "keyMoments": [1000001, 1000001, 1000002, 1000003, 2000000],
            "personMeta": [{"private_id": "secret-canary"}], "faceMeta": [], "vehicleMeta": []}
    await device.handle_message(wire("recognizeKeyFrames", body))
    detail = device.status["recognize_key_frames"]
    assert detail["metadata_presence_counts"] == {"personMeta": 1, "faceMeta": 1, "vehicleMeta": 1,
                                                  "thumbnailMeta": 0}
    assert detail["video_interval_counts"]["over_10_seconds"] == 1
    assert detail["duration_limit_counts"]["within"] == 1
    assert detail["key_moments_counts"]["above_sampling_limit"] == 1
    assert detail["key_moments_counts"]["duplicates"] == 1
    assert detail["key_moments_counts"]["outside_interval"] == 1
    assert detail["worker_rejection_counts"]["payload_fields"] == 1
    encoded = json.dumps(device.status)
    for private_value in ("secret-canary", "private_id", "1000001", "1028302", "2000000"):
        assert private_value not in encoded
    # Malformed unhashable moments also remain diagnostic input, not a new failure.
    bad = {**body, "start": None, "keyMoments": [{"private_id": "secret-canary"}]}
    await device.handle_message(wire("recognizeKeyFrames", bad, "second"))
    detail = device.status["recognize_key_frames"]
    assert detail["video_interval_counts"]["invalid_type"] == 1
    assert detail["duration_limit_counts"]["not_comparable"] == 1
    assert detail["key_moments_counts"]["non_integer"] == 1

"""Bounded diagnostics for unhandled AI Key commands and RequestAI targets (#1). Synthetic only."""

import hashlib
import json
import time
from copy import deepcopy

import pytest

from aikey.config import ConfigError, defaults, validate_config
from aikey.device import (DOCUMENTED_COMMAND_CANDIDATES, DOCUMENTED_TARGET_CANDIDATES, DeviceService,
                          command_fingerprint)
from aikey.protocol import decode_message
from aikey.worker import WorkerError
from test_basic_descriptions import device_config, wire

SECRET = "-".join(("synthetic", "controller", "secret"))


def device(tmp_path, *, until=None, handler=None):
    config = device_config()
    if until is not None:
        config["device"]["diagnostic_command_fingerprints_until"] = until

    async def reject(body):
        raise WorkerError("Unsupported RequestAI targetUri")
    return DeviceService(config, tmp_path, handler or reject)


async def send(service, action, body, identifier):
    return decode_message(await service.handle_message(wire(action, body, identifier))).header["errorCode"]


def unlisted(service):
    return service.status["unlisted"]


def test_the_comparison_key_is_stable_and_documented():
    assert command_fingerprint("fixtureUnknownCommand") == hashlib.sha256(
        b"fixtureUnknownCommand").hexdigest()[:16]
    assert "getInfo" not in DOCUMENTED_COMMAND_CANDIDATES              # handled names are counted by name already
    assert DOCUMENTED_TARGET_CANDIDATES[":7968/vlm_inference"] == "vlm_inference"


async def test_without_an_open_window_nothing_is_fingerprinted(tmp_path):
    service = device(tmp_path)
    assert await send(service, "fixtureUnknownCommand", {"token": SECRET}, "a") == 95
    assert unlisted(service)["command"] == {"candidates": {}, "fingerprints": {}, "not_recorded": 1,
                                            "malformed": 0, "overflow": 0}
    assert service.status["control_commands"]["unknown"]["count"] == 1


async def test_a_documented_candidate_is_counted_by_name_even_without_a_window(tmp_path):
    service = device(tmp_path)
    assert await send(service, "setDbCredential", {"password": SECRET}, "a") == 95
    assert unlisted(service)["command"]["candidates"] == {"setDbCredential": 1}
    assert SECRET not in json.dumps(service.status)


async def test_an_open_window_keeps_only_bounded_fingerprints(tmp_path):
    service = device(tmp_path, until=int(time.time()) + 3600)
    for index in range(10):
        await send(service, f"fixtureUnknown{index}", {"token": SECRET, "path": "/private/x"}, f"r{index}")
    await send(service, "fixtureUnknown0", {}, "again")
    bucket = unlisted(service)["command"]
    assert len(bucket["fingerprints"]) == 8 and bucket["overflow"] == 2
    assert bucket["fingerprints"][command_fingerprint("fixtureUnknown0")] == 2
    text = json.dumps(service.status)
    assert SECRET not in text and "/private/x" not in text and "fixtureUnknown" not in text


@pytest.mark.parametrize("name", ["has space", "a/b", "x=1", "9starts-with-digit", "n" * 65, "ü-name"])
async def test_a_name_outside_the_safe_shape_is_never_hashed(tmp_path, name):
    service = device(tmp_path, until=int(time.time()) + 3600)
    await send(service, name, {}, "a")
    assert unlisted(service)["command"]["malformed"] == 1
    assert unlisted(service)["command"]["fingerprints"] == {}


async def test_an_expired_window_stops_fingerprinting(tmp_path):
    service = device(tmp_path, until=int(time.time()) - 1)
    await send(service, "fixtureUnknownCommand", {}, "a")
    assert unlisted(service)["command"]["not_recorded"] == 1
    assert unlisted(service)["command"]["fingerprints"] == {}


def _request_ai(target):
    return {"targetUri": target, "payload": {"image": SECRET, "callback": "https://private.invalid/cb"},
            "timeoutMs": 1000, "resUrl": "https://private.invalid/cb"}


async def test_unsupported_request_ai_targets_are_named_or_fingerprinted_without_payload(tmp_path):
    service = device(tmp_path, until=int(time.time()) + 3600)
    assert await send(service, "RequestAI", _request_ai(":7968/vlm_inference"), "a") == 95
    assert await send(service, "RequestAI", _request_ai(":7968/some_new_route"), "b") == 95
    bucket = unlisted(service)["request_ai_target"]
    assert bucket["candidates"] == {"vlm_inference": 1}
    assert bucket["fingerprints"] == {command_fingerprint(":7968/some_new_route"): 1}
    text = json.dumps(service.status)
    for private in (SECRET, "private.invalid", "some_new_route"):
        assert private not in text


async def test_a_supported_target_or_a_handled_command_is_not_counted_as_unlisted(tmp_path):
    async def admit(body):
        return {"accepted": True}
    service = device(tmp_path, until=int(time.time()) + 3600, handler=admit)
    await send(service, "RequestAI", _request_ai(":7968/describe"), "a")
    await send(service, "getInfo", {}, "b")
    assert unlisted(service) == {"command": {"candidates": {}, "fingerprints": {}, "not_recorded": 0,
                                             "malformed": 0, "overflow": 0},
                                 "request_ai_target": {"candidates": {}, "fingerprints": {},
                                                       "not_recorded": 0, "malformed": 0, "overflow": 0}}


@pytest.mark.parametrize("value", [True, -1, 0, "1790000000", 1.5, int(time.time()) + 15 * 24 * 3600])
def test_the_window_must_be_a_bounded_unix_time(tmp_path, value):
    config = defaults(tmp_path / "state", "020000000001")
    config["device"]["diagnostic_command_fingerprints_until"] = value
    with pytest.raises(ConfigError, match="diagnostic_command_fingerprints_until"):
        validate_config(deepcopy(config))


def test_a_window_within_fourteen_days_is_accepted(tmp_path):
    config = defaults(tmp_path / "state", "020000000001")
    config["device"]["diagnostic_command_fingerprints_until"] = int(time.time()) + 7 * 24 * 3600
    assert validate_config(config)["device"]["diagnostic_command_fingerprints_until"] > time.time()

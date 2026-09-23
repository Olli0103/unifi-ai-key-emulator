"""AI Port candidate acknowledges only persisted logical hardware settings."""

import json
import os
import stat
import time

import pytest

from aikey.aiport_candidate import CandidateService
from aikey.aiport_virtual_hardware import (
    VirtualHardwareError, VirtualSoundLedStore, VirtualTimezoneStore,
    validate_sound_led, validate_timezone,
)
from test_aiport_candidate import fixture_state
from test_aiport_credentials import synthetic_digest


SETTINGS = {"ledFaceEnabled": 1, "ledFaceAlwaysOnWhenManaged": 1,
            "speakerEnabled": 0, "systemSoundsEnabled": 0,
            "speakerVolume": 50, "welcomeType": "text"}


def test_virtual_sound_led_settings_are_private_and_restart_safe(tmp_path):
    store = VirtualSoundLedStore(tmp_path)
    store.apply(SETTINGS)
    path = tmp_path / "virtual-sound-led.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert VirtualSoundLedStore(tmp_path).settings == SETTINGS


@pytest.mark.parametrize("replacement", [
    {"speakerVolume": 101}, {"ledFaceEnabled": True},
    {"welcomeType": "unknown"}, {"unexpected": 1},
])
def test_virtual_sound_led_rejects_invalid_changes(tmp_path, replacement):
    payload = SETTINGS | replacement
    with pytest.raises(VirtualHardwareError):
        validate_sound_led(payload)
    assert not (tmp_path / "virtual-sound-led.json").exists()


def test_virtual_sound_led_disk_failure_keeps_previous_state(tmp_path, monkeypatch):
    store = VirtualSoundLedStore(tmp_path)
    store.apply(SETTINGS)

    def refuse_replace(source, destination):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(VirtualHardwareError):
        store.apply(SETTINGS | {"speakerVolume": 20})
    assert store.settings == SETTINGS


def test_virtual_timezone_is_private_persistent_and_rejects_unsafe_values(tmp_path):
    store = VirtualTimezoneStore(tmp_path)
    store.apply({"timezone": "Europe/Berlin"})
    path = tmp_path / "virtual-timezone.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert VirtualTimezoneStore(tmp_path).timezone == "Europe/Berlin"
    for invalid in ("../etc/passwd", "/etc/passwd", "Europe//Berlin", "Europe/Berlin\n"):
        with pytest.raises(VirtualHardwareError):
            validate_timezone({"timezone": invalid})


@pytest.mark.asyncio
async def test_sound_led_control_replies_only_after_private_persistence(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service.credentials.rotate({"username": "synthetic-user",
                                "hashedPassword": synthetic_digest()})
    service._params_agreed = True

    class Sink:
        async def send_bytes(self, raw):
            self.last = json.loads(raw)

    sink = Sink()
    message = {"functionName": "ChangeSoundLedSettings", "messageId": 41,
               "payload": SETTINGS}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] == 0
    assert service.sound_led_replies == 1
    assert VirtualSoundLedStore(tmp_path).settings == SETTINGS
    message["payload"] = SETTINGS | {"speakerVolume": 200}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] != 0
    assert service.sound_led_rejections == 1
    assert VirtualSoundLedStore(tmp_path).settings == SETTINGS
    service.config["diagnostic_hello_until"] = int(time.time()) - 1
    message["payload"] = SETTINGS
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] != 0
    assert service.sound_led_rejections == 2


@pytest.mark.asyncio
async def test_timezone_control_replies_only_after_private_persistence(tmp_path):
    config = fixture_state(tmp_path)
    config["diagnostic_hello_until"] = int(time.time()) + 60
    service = CandidateService(config, tmp_path)
    service.credentials.rotate({"username": "synthetic-user",
                                "hashedPassword": synthetic_digest()})
    service._params_agreed = True

    class Sink:
        async def send_bytes(self, raw):
            self.last = json.loads(raw)

    sink = Sink()
    message = {"functionName": "ChangeDeviceSettings", "messageId": 42,
               "payload": {"timezone": "Europe/Berlin"}}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] == 0
    assert service.timezone_replies == 1
    assert VirtualTimezoneStore(tmp_path).timezone == "Europe/Berlin"
    message["payload"] = {"timezone": "../etc/passwd"}
    await service._handle_diagnostic_frame(sink, json.dumps(message).encode())
    assert sink.last["statusCode"] != 0
    assert service.timezone_rejections == 1
    assert VirtualTimezoneStore(tmp_path).timezone == "Europe/Berlin"

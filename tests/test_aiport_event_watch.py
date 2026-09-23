"""A Protect subscription is read-only evidence, not emulator event attribution."""

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

from aikey import aiport_event_watch
from aikey.aiport_event_watch import _event, _selected_camera_ids, watch_events
from aikey.aiport_deployment import AiPortPlanError


CAMERA_ID = f"{1:024x}"
OTHER_ID = f"{2:024x}"


def test_exact_camera_watch_requires_one_eligible_current_name():
    inventory = {"cameras": [
        {"id": CAMERA_ID, "name": "Flur"},
        {"id": OTHER_ID, "name": "Garage"},
    ]}
    plan = {"instances": [{"camera_ids": [CAMERA_ID]}]}
    assert _selected_camera_ids(inventory, plan, "Flur") == {CAMERA_ID}
    assert _selected_camera_ids(inventory, plan, None) == {CAMERA_ID}
    for name in ("Garage", "flur", "Missing"):
        with pytest.raises(AiPortPlanError, match="one eligible"):
            _selected_camera_ids(inventory, plan, name)
    inventory["cameras"][1]["name"] = "Flur"
    plan["instances"][0]["camera_ids"].append(OTHER_ID)
    with pytest.raises(AiPortPlanError, match="one eligible"):
        _selected_camera_ids(inventory, plan, "Flur")


def frame(*, camera=CAMERA_ID, action="add", event="smartDetectZone",
          event_id="event-one", types=None, omit_types=False, null_types=False):
    item = {
        "id": event_id, "modelKey": "event", "device": camera,
        "type": event,
    }
    if not omit_types:
        item["smartDetectTypes"] = (None if null_types else
                                    ["person"] if types is None else types)
    return json.dumps({"type": action, "item": item})


def test_event_parser_accepts_only_targeted_camera_events():
    assert _event(frame(), {CAMERA_ID}) == (
        CAMERA_ID, "add", "event-one", "smartDetectZone", True, "person")
    assert _event(frame(camera=f"{2:024x}"), {CAMERA_ID}) is None
    assert _event(frame(event="motion"), {CAMERA_ID})[-2] is False
    assert _event(frame(types=["vehicle"]), {CAMERA_ID})[-2:] == (False, "other")
    assert _event(frame(types=[]), {CAMERA_ID})[-2:] == (False, "empty")
    assert _event(frame(omit_types=True), {CAMERA_ID})[-2:] == (False, "omitted")
    assert _event(frame(null_types=True), {CAMERA_ID})[-2:] == (False, "null")
    assert _event(json.dumps({"type": "delete", "item": {}}), {CAMERA_ID}) is None


@pytest.mark.parametrize("bad", [
    "{bad json",
    frame(types=["x" * 65]),
    frame(event_id=""),
    "x" * (256 * 1024 + 1),
])
def test_event_parser_rejects_malformed_targeted_data(bad):
    with pytest.raises(ValueError):
        _event(bad, {CAMERA_ID})


@pytest.mark.asyncio
async def test_watch_counts_native_subscription_events_without_retaining_payloads(monkeypatch):
    async def fake_inventory(*args, **kwargs):
        return {"schema": "aikey-camera-preflight/1", "cameras": [
            {"id": CAMERA_ID, "name": "Flur", "model": "UVC G3 Instant",
             "state": "CONNECTED", "processing_class": "legacy_ingress_needed"},
            {"id": OTHER_ID, "name": "Garage", "model": "UVC G4 Dome",
             "state": "CONNECTED", "processing_class": "legacy_ingress_needed"}]}

    class FakeSocket:
        def __init__(self):
            self.frames = iter([
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, frame(types=[]), ""),
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, frame(types=[]), ""),
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT,
                                  frame(action="update"), ""),
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT,
                                  frame(action="update", omit_types=True), ""),
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT,
                                  frame(camera=OTHER_ID, event_id="other-event"), ""),
            ])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def receive(self, *, timeout):
            assert timeout > 0
            try:
                return next(self.frames)
            except StopIteration as exc:
                raise asyncio.TimeoutError from exc

    class FakeSession:
        def __init__(self, *, connector, timeout, trust_env, headers):
            assert connector == "pinned connector"
            assert trust_env is False
            assert headers["X-API-Key"] == "private-test-key"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def ws_connect(self, url, **kwargs):
            assert url == "wss://192.0.2.1/proxy/protect/integration/v1/subscribe/events"
            assert kwargs["compress"] == 0
            assert kwargs["max_msg_size"] == 256 * 1024
            return FakeSocket()

    monkeypatch.setattr(aiport_event_watch, "fetch_inventory", fake_inventory)
    monkeypatch.setattr(aiport_event_watch, "_read_private", lambda *args: b"private-test-key")
    monkeypatch.setattr(aiport_event_watch, "_trusted_web", lambda *args: (object(), b"pin"))
    monkeypatch.setattr(aiport_event_watch, "PinnedWebConnector",
                        lambda *args: "pinned connector")
    monkeypatch.setattr(aiport_event_watch.aiohttp, "ClientSession", FakeSession)
    result = await watch_events("192.0.2.1", api_key_file=Path("key"),
                                trust_file=Path("trust"), cert_file=Path("cert"),
                                camera_name="Flur", seconds=1)
    assert result["subscription_opened"] is True
    assert result["messages_seen"] == 5
    assert result["targeted_event_adds"] == 1
    assert result["targeted_event_updates"] == 2
    assert result["targeted_smart_video_adds"] == 1
    assert result["targeted_smart_video_updates"] == 2
    assert result["targeted_person_adds"] == 0
    assert result["targeted_person_updates"] == 1
    assert result["smart_type_states"] == {
        "add": {"omitted": 0, "null": 0, "empty": 1, "person": 0, "other": 0},
        "update": {"omitted": 1, "null": 0, "empty": 0, "person": 1, "other": 0},
    }
    assert result["selected_camera_count"] == 1
    assert result["timeline_persistence"] == "needs_evidence"
    assert CAMERA_ID not in json.dumps(result)
    assert "private-test-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_watch_rejects_unbounded_duration_before_network():
    with pytest.raises(ValueError, match="1 to 600"):
        await watch_events("192.0.2.1", api_key_file=Path("key"),
                           trust_file=Path("trust"), cert_file=Path("cert"),
                           seconds=601)

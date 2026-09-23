"""Bounded, read-only Protect event subscription for AI Port trials.

This observes controller-published events. It does not attribute an event to
this emulator or prove that the event persists on a camera timeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

import aiohttp

from .aiport_deployment import AiPortPlanError, plan_ai_ports
from .camera_inventory import (InventoryError, PinnedWebConnector, _read_private,
                               _trusted_web, fetch_inventory)


_SMART_VIDEO_EVENTS = frozenset({"smartDetectZone", "smartDetectLine",
                                 "smartDetectLoiterZone"})
_MAX_FRAME_BYTES = 256 * 1024
_MAX_MESSAGES = 1000


def _selected_camera_ids(inventory: dict, plan: dict,
                         camera_name: str | None) -> set[str]:
    camera_ids = {camera_id for instance in plan["instances"]
                  for camera_id in instance["camera_ids"]}
    if camera_name is None:
        return camera_ids
    matching = {camera["id"] for camera in inventory["cameras"]
                if camera.get("name") == camera_name and camera["id"] in camera_ids}
    if len(matching) != 1:
        raise AiPortPlanError("Camera name must identify one eligible connected camera")
    return matching


def _event(raw: str, camera_ids: set[str]) -> tuple[str, str, str, str, bool] | None:
    """Return only the fields needed to count a targeted camera event."""
    if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
        raise ValueError("Event frame is too large")
    try:
        message = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid event JSON") from exc
    if not isinstance(message, dict) or message.get("type") not in {"add", "update"}:
        return None
    item = message.get("item")
    if not isinstance(item, dict) or item.get("modelKey") != "event":
        return None
    camera_id = item.get("device")
    if not isinstance(camera_id, str) or camera_id not in camera_ids:
        return None
    event_id = item.get("id")
    event_type = item.get("type")
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 128
            or not isinstance(event_type, str) or len(event_type) > 64):
        raise ValueError("Invalid targeted event shape")
    smart_types = item.get("smartDetectTypes")
    if smart_types is not None and (not isinstance(smart_types, list)
                                    or len(smart_types) > 16
                                    or any(not isinstance(value, str) or len(value) > 64
                                           for value in smart_types)):
        raise ValueError("Invalid targeted smart types")
    return (camera_id, message["type"], event_id, event_type,
            event_type in _SMART_VIDEO_EVENTS
            and isinstance(smart_types, list) and "person" in smart_types)


async def watch_events(host: str, *, api_key_file: Path, trust_file: Path,
                       cert_file: Path, camera_scope: str = "legacy-only",
                       camera_name: str | None = None, seconds: int = 60) -> dict:
    """Watch a validated camera set for at most ten minutes without storing bodies."""
    if type(seconds) is not int or not 1 <= seconds <= 600:
        raise AiPortPlanError("Event watch must last 1 to 600 seconds")
    if (camera_name is not None and (not isinstance(camera_name, str)
            or not 1 <= len(camera_name) <= 128
            or any(ord(char) < 32 for char in camera_name))):
        raise AiPortPlanError("Camera name must be an exact, nonempty display name")
    inventory = await fetch_inventory(host, api_key_file=api_key_file,
                                      trust_file=trust_file, cert_file=cert_file)
    plan = plan_ai_ports(inventory, camera_scope=camera_scope)
    camera_ids = _selected_camera_ids(inventory, plan, camera_name)
    result = {"schema": "aikey-aiport-event-watch/1", "camera_scope": camera_scope,
              "selected_camera_count": len(camera_ids), "subscription_opened": False,
              "messages_seen": 0, "invalid_messages": 0,
              "targeted_event_adds": 0, "targeted_event_updates": 0,
              "targeted_smart_video_adds": 0, "targeted_person_adds": 0,
              "attribution_to_ai_port": "needs_evidence",
              "timeline_persistence": "needs_evidence"}
    if not camera_ids:
        return result
    try:
        key = _read_private(api_key_file, 4096).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise InventoryError("Invalid Protect integration API key file") from exc
    if not key or len(key) > 512 or any(ch.isspace() for ch in key):
        raise InventoryError("Invalid Protect integration API key file")
    context, pin = _trusted_web(host, trust_file, cert_file)
    connector = PinnedWebConnector(context, pin)
    timeout = aiohttp.ClientTimeout(total=None, connect=5, sock_read=seconds + 5)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                    trust_env=False,
                                    headers={"X-API-Key": key,
                                             "Accept": "application/json"}) as session:
        try:
            async with session.ws_connect(
                    f"wss://{host}/proxy/protect/integration/v1/subscribe/events",
                    max_msg_size=_MAX_FRAME_BYTES, compress=0, heartbeat=30,
                    autoclose=False) as ws:
                result["subscription_opened"] = True
                deadline = time.monotonic() + seconds
                seen_ids: set[str] = set()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        message = await ws.receive(timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                        aiohttp.WSMsgType.ERROR}:
                        raise InventoryError("Protect event subscription closed before the watch ended")
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    result["messages_seen"] += 1
                    if result["messages_seen"] > _MAX_MESSAGES:
                        raise InventoryError("Protect event watch message limit reached")
                    try:
                        parsed = _event(message.data, camera_ids)
                    except ValueError:
                        result["invalid_messages"] += 1
                        continue
                    if parsed is None:
                        continue
                    _, action, event_id, event_type, is_person = parsed
                    if action == "update":
                        result["targeted_event_updates"] += 1
                    elif event_id not in seen_ids:
                        seen_ids.add(event_id)
                        result["targeted_event_adds"] += 1
                        if event_type in _SMART_VIDEO_EVENTS:
                            result["targeted_smart_video_adds"] += 1
                        if is_person:
                            result["targeted_person_adds"] += 1
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            raise InventoryError(f"Protect event subscription failed ({type(exc).__name__})") from exc
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch Protect events for selected AI Port candidate cameras")
    parser.add_argument("--controller", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--web-trust-file", type=Path, required=True)
    parser.add_argument("--web-cert-file", type=Path, required=True)
    parser.add_argument("--camera-scope", choices=("legacy-only", "legacy-and-g3-g5"),
                        default="legacy-only")
    parser.add_argument("--camera-name", help="Exact eligible Protect camera name to watch")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(watch_events(
            args.controller, api_key_file=args.api_key_file,
            trust_file=args.web_trust_file, cert_file=args.web_cert_file,
            camera_scope=args.camera_scope, camera_name=args.camera_name,
            seconds=args.seconds))
    except (AiPortPlanError, InventoryError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reuse the observed camera contract for private read-only admin inventory."""

import time

from .camera_inventory import InventoryError, is_supported_protect_version, parse_cameras


def inventory_view(report: dict, bindings: dict):
    if (not isinstance(report, dict) or report.get("schema") != "aikey-camera-preflight/1"
            or not is_supported_protect_version(report.get("protect_version"))
            or type(report.get("fetched_at")) is not int
            or not 0 <= time.time() - report["fetched_at"] <= 300
            or not isinstance(report.get("cameras"), list) or len(report["cameras"]) > 256):
        raise InventoryError("Invalid admin inventory")
    rows = []
    for item in report["cameras"]:
        if not isinstance(item, dict):
            raise InventoryError("Invalid admin inventory")
        rows.append({"id": item.get("id"), "modelKey": "camera", "mac": item.get("mac"),
            "name": item.get("name"), "type": item.get("model"), "state": item.get("state"),
            "featureFlags": {"smartDetectTypes": item.get("smart_detect_types"),
                             "smartDetectAudioTypes": item.get("audio_detect_types")}})
    cameras = []
    for camera in parse_cameras(rows):
        cameras.append({**camera.public(), "saved_profiles": sorted(bindings.get(camera.mac, ())),
            "binding_observation": "saved_configuration_not_native_pairing_proof",
            "source_kind": "not_provided_by_inventory"})
    return {"protect_version": report["protect_version"], "fetched_at": report["fetched_at"],
            "processing_enabled_by_this_read": False, "onvif_ingest": "not_implemented",
            "cameras": cameras}

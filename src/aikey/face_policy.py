"""Pure shared admission for existing local recognition/enhancement roles."""

import ipaddress
import re
from urllib.parse import urlsplit


class FacePolicyError(ValueError):
    """Fixed policy failure, never a server address, name or embedding."""


def _server(value, field):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        address = ipaddress.ip_address(parsed.hostname or "") if parsed else None
        valid = (parsed is not None and parsed.scheme == "http" and address is not None
                 and (address.is_loopback or address.is_private) and parsed.path in {"", "/"}
                 and not (parsed.query or parsed.fragment or parsed.username or parsed.password)
                 and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise FacePolicyError(f"{field}.server must be a local HTTP server")
    return value.rstrip("/")


def recognition_policy(value):
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) - {"server", "camera_ids", "max_faces", "native_face_cameras"}
            or not {"server", "camera_ids"} <= set(value)):
        raise FacePolicyError("face_recognition needs server and camera_ids")
    policy = value.get("native_face_cameras", "skip")
    if policy not in ("skip", "process"):
        raise FacePolicyError("face_recognition.native_face_cameras must be skip or process")
    server = _server(value["server"], "face_recognition")
    cameras = value["camera_ids"]
    if (not isinstance(cameras, list) or not 1 <= len(cameras) <= 8
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c)
                   for c in cameras) or len(set(cameras)) != len(cameras)):
        raise FacePolicyError("face_recognition.camera_ids must list 1 to 8 camera IDs")
    limit = value.get("max_faces", 8)
    if type(limit) is not int or not 1 <= limit <= 16:
        raise FacePolicyError("face_recognition.max_faces must be 1..16")
    return {"server": server, "camera_ids": tuple(cameras), "max_faces": limit,
            "native_face_cameras": policy}


def enhancement_policy(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"server"}:
        raise FacePolicyError("face_enhancement needs only server")
    return {"server": _server(value["server"], "face_enhancement")}

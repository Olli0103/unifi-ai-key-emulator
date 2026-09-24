"""Bounded API object observations for the AI Port camera engine.

This is an optional detector. API-reported scores and boxes are uncalibrated
until measured against camera footage; configuration alone is not parity proof.
"""

from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import stat
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

from .aiport_detection import ObjectObservation
from .aiport_event_budget import EventBudget
from .aiport_tracking import TrackingError, validate_observation
from .providers import ProviderError, image_mime, validate_inference_config


_MAX_FRAME_BYTES = 1024 * 1024
_MAX_REPLY_BYTES = 64 * 1024
_MOTION_CHANGED_CELLS = 8
_LABELS = {
    "person": {"person"},
    "vehicle": {"bicycle", "car", "motorcycle", "bus", "truck"},
    "animal": {"bird", "cat", "dog", "horse", "sheep", "cow"},
}
_PROMPT = (
    "Detect visible people, vehicles and animals. Return only compact JSON with "
    "this exact shape: {\"detections\":[{\"kind\":\"person\",\"label\":\"person\","
    "\"score\":0.9,\"box\":[0.1,0.1,0.5,0.8]}]}. "
    "Box values are fractions of image width and height in left, top, right, bottom order. "
    "Use kind person, vehicle or animal. Valid labels are person; bicycle, car, "
    "motorcycle, bus, truck; bird, cat, dog, horse, sheep, cow. "
    "Include an object only when its full visible extent can be located. "
    "Return an empty detections array when uncertain. Do not infer off-screen objects."
)


class ApiDetectionError(ValueError):
    """Fixed failure code without media, provider reply or credentials."""


class _MotionGate:
    """Use cheap frame differences to request two API frames per motion burst."""

    def __init__(self):
        self._previous: dict[str, bytes] = {}
        self._pending: dict[str, int] = {}

    def should_request(self, camera: str, frame: bytes) -> bool:
        try:
            with Image.open(BytesIO(frame)) as image:
                width, height = image.size
                if (image.format != "JPEG" or not 16 <= width <= 8192
                        or not 16 <= height <= 4320 or width * height > 4_000_000):
                    raise ValueError
                thumbnail = image.convert("L").resize((32, 18)).tobytes()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ApiDetectionError("invalid_api_detection_frame") from exc
        previous = self._previous.get(camera)
        self._previous[camera] = thumbnail
        if previous is None:
            return False
        changed = sum(abs(a - b) >= 24 for a, b in zip(previous, thumbnail, strict=True))
        if changed >= _MOTION_CHANGED_CELLS and self._pending.get(camera, 0) == 0:
            self._pending[camera] = 2
        if self._pending.get(camera, 0):
            self._pending[camera] -= 1
            return True
        return False


def parse_detections(text: str, *, threshold: float) -> tuple[ObjectObservation, ...]:
    try:
        result = json.loads(text)
        if not isinstance(result, dict) or set(result) != {"detections"}:
            raise ValueError
        entries = result["detections"]
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError
        observations = []
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"kind", "label", "score", "box"}:
                raise ValueError
            kind, label, score, box = (item[name] for name in
                                       ("kind", "label", "score", "box"))
            if (not isinstance(kind, str) or kind not in _LABELS
                    or not isinstance(label, str) or label not in _LABELS[kind]
                    or type(score) not in (float, int)
                    or not isinstance(box, list) or len(box) != 4
                    or any(type(point) not in (float, int) for point in box)):
                raise ValueError
            observation = ObjectObservation(kind, label, score, tuple(box))
            validate_observation(observation)
            if score >= threshold:
                observations.append(observation)
        return tuple(observations)
    except (ValueError, KeyError, TypeError, TrackingError) as exc:
        raise ApiDetectionError("invalid_api_detection_response") from exc


def _read_private_key(path: str) -> str:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ApiDetectionError("invalid_api_key_file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 4096):
                raise ApiDetectionError("invalid_api_key_file")
            value = source.read(4097).decode("ascii").strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise ApiDetectionError("invalid_api_key_file") from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _post(url: str, headers: dict, payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    request = Request(url, data=body, headers={**headers, "Content-Type": "application/json"},
                      method="POST")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise ApiDetectionError("api_detection_http_failure")
            raw = response.read(_MAX_REPLY_BYTES + 1)
            if len(raw) > _MAX_REPLY_BYTES:
                raise ApiDetectionError("api_detection_response_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
    except ApiDetectionError:
        raise
    except Exception as exc:
        raise ApiDetectionError("api_detection_request_failed") from exc


class ApiObjectDetector:
    """One paid request per sampled frame, with a durable per-camera hourly cap."""

    def __init__(self, provider_config: dict[str, Any], state_dir: Path, *,
                 threshold: float, max_requests_per_hour: int,
                 transport: Callable[[str, dict, dict], dict] = _post):
        if (type(threshold) not in (float, int) or not 0 < threshold <= 1
                or type(max_requests_per_hour) is not int
                or not 2 <= max_requests_per_hour <= 3600):
            raise ApiDetectionError("invalid_api_detection_policy")
        config = dict(provider_config)
        if "api_key" in config:
            raise ApiDetectionError("inline_api_key_forbidden")
        if config.get("api_key_file"):
            config["api_key"] = _read_private_key(config["api_key_file"])
        try:
            self.provider = validate_inference_config(config)
        except ProviderError as exc:
            raise ApiDetectionError("invalid_api_detection_provider") from exc
        endpoint = urlsplit(self.provider.base_url)
        if not ((self.provider.provider == "openai" and endpoint.hostname == "api.openai.com")
                or (self.provider.provider == "anthropic" and endpoint.hostname == "api.anthropic.com")
                or endpoint.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ApiDetectionError("api_detection_endpoint_not_approved")
        self.threshold = float(threshold)
        self.budget = EventBudget(
            state_dir, limit=max_requests_per_hour, namespace="vision-request")
        self.transport = transport
        self.motion = _MotionGate()

    def detect_for_camera(self, camera_mac: str,
                          frame: bytes) -> tuple[ObjectObservation, ...]:
        if not isinstance(frame, bytes) or not 0 < len(frame) <= _MAX_FRAME_BYTES:
            raise ApiDetectionError("invalid_api_detection_frame")
        try:
            if image_mime(frame) != "image/jpeg":
                raise ApiDetectionError("invalid_api_detection_frame")
        except ProviderError as exc:
            raise ApiDetectionError("invalid_api_detection_frame") from exc
        if not self.motion.should_request(camera_mac, frame):
            return ()
        if not self.budget.claim(camera_mac):
            return ()
        try:
            url, headers, payload = self.provider.build_request([frame], _PROMPT)
            if self.provider.provider == "openai" and self.provider.model == "gpt-6-luna":
                payload["reasoning"] = {"effort": "none"}
            reply = self.transport(url, headers, payload)
            text = self.provider.parse_response(reply)
            return parse_detections(text, threshold=self.threshold)
        except ApiDetectionError:
            raise
        except (ProviderError, TypeError, ValueError) as exc:
            raise ApiDetectionError("api_detection_request_failed") from exc

"""Bounded API object observations for the AI Port camera engine.

This is an optional detector. API-reported scores and boxes are uncalibrated
until measured against camera footage; configuration alone is not parity proof.
"""

from __future__ import annotations

import ipaddress
import json
import re
from io import BytesIO
import os
from pathlib import Path
import socket
import stat
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

from .aiport_detection import ObjectObservation
from .aiport_plates import normalize_plate
from .aiport_event_budget import EventBudget
from .aiport_timeline import MinuteHistory
from .aiport_tracking import TrackingError, validate_observation
from .providers import ProviderError, image_mime, validate_inference_config


_MAX_FRAME_BYTES = 1024 * 1024
_MAX_REPLY_BYTES = 64 * 1024
_MOTION_CHANGED_CELLS = 8
_DNS_RETRY_SECONDS = 60
# Motion after this much stillness starts a new scene, e.g. a cat entering a
# quiet room. Shorter gaps are the same presence moving on (a person walking
# about); matches the motion stop hold in aiport_motion.
_FRESH_QUIET_SECONDS = 8.0
# With an optional request cap, the share of each camera's hourly cap kept
# for new scenes. Without it people moving through Flur spent 16 of 20
# requests in one visit and left the camera blind for 12 motion events.
_FRESH_RESERVE_DIVISOR = 3
# Without a request cap, provider failures (HTTP errors, timeouts, invalid
# envelopes) pause every camera of this detector, since they share one
# provider and key: 5 s after the first failure, doubling to five minutes.
_BACKOFF_FIRST_SECONDS = 5.0
_BACKOFF_MAX_SECONDS = 300.0
# An HTTP 429 body is read only to compare its machine-readable error code and
# type with these fixed values; message text is never kept or reported.
_MAX_ERROR_BYTES = 4096
_QUOTA_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached",
                          "billing_not_active"})
_RATE_CODES = frozenset({"rate_limit_exceeded", "rate_limit_error",
                         "requests", "tokens"})
HTTP_429_CATEGORIES = ("quota", "rate", "unknown")
_LABELS = {
    "person": {"person"},
    "vehicle": {"bicycle", "car", "motorcycle", "bus", "truck"},
    "animal": {"bird", "cat", "dog", "horse", "sheep", "cow"},
    "package": {"package"},
}
_PROMPT = (
    "Detect visible people, vehicles, animals and delivery packages. Return only compact JSON with "
    "this exact shape: {\"detections\":[{\"kind\":\"person\",\"label\":\"person\","
    "\"score\":0.9,\"box\":[0.1,0.1,0.5,0.8]}]}. "
    "Box values are fractions of image width and height in left, top, right, bottom order. "
    "Use kind person, vehicle, animal or package. Valid labels are person; bicycle, car, "
    "motorcycle, bus, truck; bird, cat, dog, horse, sheep, cow; package. "
    # A cat sitting in Flur's night-IR frame came back as package and
    # person, never animal (25 Sep 2026, user-confirmed ground truth).
    "Frames are often grayscale night infrared from indoor cameras, where pets are common. "
    "A person has a human body shape: head, torso and limbs. A cat or dog, even curled up, "
    "sitting still or partly hidden, is kind animal, never package or person. A package is "
    "an inanimate delivered box, parcel, envelope or bag with straight edges or folds. "
    "Include an object only when its full visible extent can be located. "
    "Return an empty detections array when uncertain. Do not infer off-screen objects."
)
# Only for cameras an operator listed in plate_cameras (#19). The same frame
# and request; Protect stores the text as the vehicle track's licensePlate.
_PLATE_PROMPT = (
    " For a vehicle whose license plate is visible, add \"plate\" with its characters "
    "exactly as printed, using ? for every character you cannot read with certainty. "
    "Never guess a character. Omit plate when no plate is legible."
)


# The local fallback (Qwen3-VL through Ollama) locates objects on its native
# 0..1000 grid; boxes are converted to fractions before the shared parser.
_FALLBACK_PROMPT = (
    "Detect visible people, vehicles, animals and delivery packages in this security camera "
    "frame. For each object give kind (person, vehicle, animal or package), label (person; "
    "bicycle, car, motorcycle, bus, truck; bird, cat, dog, horse, sheep, cow; package), score "
    "between 0 and 1, and box [left, top, right, bottom] on a 0-1000 grid of the image. "
    "Frames are often grayscale night infrared. A cat or dog is kind animal, never package or "
    "person. Return an empty detections list when nothing is clearly visible."
)
_FALLBACK_SCHEMA = {
    "type": "object", "required": ["detections"], "additionalProperties": False,
    "properties": {"detections": {"type": "array", "maxItems": 20, "items": {
        "type": "object", "required": ["kind", "label", "score", "box"],
        "additionalProperties": False,
        "properties": {"kind": {"type": "string"}, "label": {"type": "string"},
                       "score": {"type": "number"},
                       "box": {"type": "array", "minItems": 4, "maxItems": 4,
                               "items": {"type": "number"}}}}}}}
FALLBACK_REASONS = ("http_429", "http_5xx", "request_failed", "dns_unavailable", "backoff")


def fallback_to_fractions(text: str) -> str:
    """The fallback reply in the shared schema, with 0..1000 boxes as fractions."""
    try:
        result = json.loads(text)
        items = result["detections"] if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ValueError
        for item in items:
            box = item.get("box") if isinstance(item, dict) else None
            if (isinstance(box, list) and len(box) == 4
                    and all(type(v) in (int, float) for v in box)):
                item["box"] = [max(0.0, min(1.0, v / 1000)) for v in box]
        return json.dumps({"detections": items})
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ApiDetectionError("invalid_api_detection_response") from exc


class ApiDetectionError(ValueError):
    """Fixed failure code without media, provider reply or credentials."""


class _MotionGate:
    """Probe once at startup and confirm positives; sample motion in pairs."""

    def __init__(self):
        self._previous: dict[str, bytes] = {}
        self._pending: dict[str, int] = {}
        self._quiet: dict[str, int] = {}
        self._armed: dict[str, bool] = {}
        self._startup_probe: set[str] = set()
        self._last_change: dict[str, float] = {}
        self._fresh: dict[str, bool] = {}
        # The last frame's changed-cell count (None for a baseline frame), for
        # the per-minute gate history (#6). Never pixel data.
        self.last_changed: dict[str, int | None] = {}

    def reset(self, camera: str) -> None:
        """Rearm startup sampling after a request was blocked before inference."""
        self._previous.pop(camera, None)
        self._pending.pop(camera, None)
        self._quiet.pop(camera, None)
        self._armed.pop(camera, None)
        self._startup_probe.discard(camera)
        self._last_change.pop(camera, None)
        self._fresh.pop(camera, None)

    def wait_for_motion(self, camera: str) -> None:
        """After a full budget, preserve the scene but cancel automatic probes.

        Resetting the gate here would spend each newly freed hourly request on
        an idle startup frame. Keep motion armed so a continuously changing
        scene can still use a later recovered allowance.
        """
        self._pending.pop(camera, None)
        self._quiet[camera] = 0
        self._armed[camera] = True
        self._startup_probe.discard(camera)

    def cancel_confirmation(self, camera: str) -> bool:
        """Drop a pending confirming request; the scene stays disarmed."""
        return self._pending.pop(camera, 0) > 0

    def is_refresh(self, camera: str) -> bool:
        """The request about to be sent re-samples an ongoing presence.

        Only the first request of a motion pair qualifies; a confirming
        request for a newly seen object is never deferred.
        """
        return self._pending.get(camera, 0) == 1 and not self._fresh.get(camera, True)

    def defer(self, camera: str) -> None:
        """Skip a refresh pair; motion rearms after the scene quiets."""
        self._pending.pop(camera, None)

    def sample_result(self, camera: str, *, found_object: bool) -> None:
        """Only spend a confirming request if the first one found an object.

        One positive sample cannot confirm a track, and the next unsampled
        frame drops it. A second request after an empty or failed first one
        can never produce an event, so it would only spend the budget. Motion
        stays disarmed until the scene quiets; an idle startup probe rearms.
        """
        if not found_object:
            self._pending.pop(camera, None)
        if camera not in self._startup_probe:
            return
        self._startup_probe.remove(camera)
        if not found_object:
            self._armed[camera] = True

    def has_baseline(self, camera: str) -> bool:
        return camera in self._previous

    def should_request(self, camera: str, frame: bytes, *,
                       allow_startup_probe: bool = True) -> bool:
        try:
            with Image.open(BytesIO(frame)) as image:
                width, height = image.size
                if (image.format != "JPEG" or not 16 <= width <= 8192
                        or not 16 <= height <= 4320 or width * height > 4_000_000):
                    raise ValueError
                image.draft("L", (64, 36))
                thumbnail = image.convert("L").resize((32, 18)).tobytes()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ApiDetectionError("invalid_api_detection_frame") from exc
        now = time.monotonic()
        previous = self._previous.get(camera)
        self._previous[camera] = thumbnail
        if previous is None:
            self.last_changed[camera] = None
            # A stationary object already in view would never pass a
            # frame-difference gate. Only a positive first probe needs a
            # second observation for tracker confirmation. A restart with a
            # partly used durable budget waits for fresh motion instead.
            if allow_startup_probe:
                self._pending[camera] = 2
                self._armed[camera] = False
                self._fresh[camera] = True
                self._startup_probe.add(camera)
            else:
                self._armed[camera] = True
        else:
            changed = sum(abs(a - b) >= 24 for a, b in zip(previous, thumbnail, strict=True))
            self.last_changed[camera] = changed
            if changed < _MOTION_CHANGED_CELLS:
                self._quiet[camera] = min(3, self._quiet.get(camera, 0) + 1)
                if self._quiet[camera] == 3:
                    self._armed[camera] = True
            else:
                self._quiet[camera] = 0
            if (changed >= _MOTION_CHANGED_CELLS
                    and self._armed.get(camera, True)
                    and self._pending.get(camera, 0) == 0):
                self._pending[camera] = 2
                self._armed[camera] = False
                last = self._last_change.get(camera)
                self._fresh[camera] = (last is None
                                       or now - last >= _FRESH_QUIET_SECONDS)
            if changed >= _MOTION_CHANGED_CELLS:
                self._last_change[camera] = now
        if self._pending.get(camera, 0):
            self._pending[camera] -= 1
            return True
        return False


# Content-free profile of each paid request: IR (no chroma) or colour frame,
# and whether the reply was empty, below the score gate or accepted.
_PROFILE_KEYS = tuple(f"{mode}_{outcome}" for mode in ("color", "ir")
                      for outcome in ("empty", "low", "objects"))
_IR_CHROMA = 3.0


def _frame_mode(frame: bytes) -> str:
    """'ir' when the frame carries no colour (night IR), else 'color'."""
    try:
        with Image.open(BytesIO(frame)) as image:
            image.draft("YCbCr", (64, 36))
            small = image.convert("YCbCr").resize((64, 36))
    except (OSError, ValueError, UnidentifiedImageError):
        return "color"
    _, cb, cr = small.split()
    pixels = small.width * small.height
    chroma = (sum(abs(v - 128) for v in cb.tobytes())
              + sum(abs(v - 128) for v in cr.tobytes())) / (2 * pixels)
    return "ir" if chroma < _IR_CHROMA else "color"


# The detection schema allows 20 compact detections (~30-35 tokens each), so
# a reply needs up to ~700 output tokens. A smaller configured budget made
# busy scenes (Garage, ~5 vehicles per reply) end "incomplete" and fail.
_DETECTION_OUTPUT_TOKENS = 1024
# Fixed, content-free categories for a refused reply.
_FAILURE_REASONS = (
    "incomplete_max_output_tokens", "incomplete_content_filter", "incomplete_other",
    "status_failed", "status_cancelled", "status_other", "error", "refusal", "shape",
    "stop_max_tokens", "fenced", "not_json", "not_object", "extra_fields", "too_many_entries")
_PACKAGE_CHECK_KEYS = ("confirmed", "relabelled_animal", "rejected", "failed", "lens_owned")
_VERIFY_SIDE = 512
_VERIFY_PROMPT = (
    "This is a close-up crop around one object reported as a delivery package by a home "
    "security camera; it may be grayscale night infrared. Classify the single main object. "
    "Return only compact JSON with this exact shape: {\"kind\":\"package\",\"label\":\"package\"}. "
    "Use kind person, vehicle, animal or package with a valid label: person; bicycle, car, "
    "motorcycle, bus, truck; bird, cat, dog, horse, sheep, cow; package. A cat or dog, even "
    "curled up, sitting still or partly hidden, is kind animal. A package is an inanimate "
    "delivered box, parcel, envelope or bag. If it is none of these, return "
    "{\"kind\":\"none\",\"label\":\"none\"}."
)
_REJECTED_BUCKETS = tuple((name, re.compile(pattern)) for name, pattern in (
    ("plate", r"plate|licen[cs]e|lpr|registration"),
    ("face", r"face|head"),
    ("person_like", r"human|people|pedestrian|man\b|woman|child|body"),
    ("vehicle_like", r"vehicle|car\b|van\b|suv|truck|bus\b|bike|cycle|scooter|trailer|tractor"),
    ("animal_like", r"animal|pet\b|cat\b|dog\b|bird|horse|mouse|rat\b|fox|squirrel"),
    ("package_like", r"package|parcel|box\b|bag\b|envelope|delivery"),
))
REJECTED_CATEGORY_KEYS = tuple(f"{field}:{name}" for field in ("kind", "label")
                               for name in (*(n for n, _ in _REJECTED_BUCKETS), "other", "not_text"))
_ITEM_REJECTIONS = ("shape", "kind", "label:person", "label:vehicle", "label:animal",
                    "label:package", "score", "box", "plate")


def _envelope_failure(reply: object) -> str:
    """Why a provider envelope was refused, as a fixed category (no content)."""
    if not isinstance(reply, dict):
        return "shape"
    if reply.get("error") is not None:
        return "error"
    if reply.get("stop_reason") == "max_tokens":
        return "stop_max_tokens"
    status = reply.get("status")
    if status == "incomplete":
        details = reply.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        return {"max_output_tokens": "incomplete_max_output_tokens",
                "content_filter": "incomplete_content_filter"}.get(reason, "incomplete_other")
    if status in {"failed", "cancelled"}:
        return "status_" + status
    if status not in (None, "completed"):
        return "status_other"
    output = reply.get("output")
    if isinstance(output, list) and any(
            isinstance(item, dict) and isinstance(item.get("content"), list)
            and any(isinstance(part, dict) and part.get("type") == "refusal"
                    for part in item["content"]) for item in output):
        return "refusal"
    return "shape"


def _parse_failure(text: str) -> str:
    """Why a reply text was not a detection object, as a fixed category."""
    if text.lstrip().startswith("```"):
        return "fenced"
    try:
        value = json.loads(text)
    except ValueError:
        return "not_json"
    if not isinstance(value, dict):
        return "not_object"
    if set(value) != {"detections"}:
        return "extra_fields"
    entries = value.get("detections")
    if isinstance(entries, list) and len(entries) > 20:
        return "too_many_entries"
    return "shape"


def rejected_category(value: object) -> str:
    """Fixed bucket for an unsupported kind or label; the value is never kept.

    Lets health show what the model named outside the supported vocabulary
    (#79: 115 unsupported kinds on a plate camera) without retaining text.
    """
    if not isinstance(value, str):
        return "not_text"
    text = value.lower()
    for bucket, pattern in _REJECTED_BUCKETS:
        if pattern.search(text):
            return bucket
    return "other"


def parse_detections(text: str, *, threshold: float,
                     rejected: dict[str, int] | None = None,
                     plates: bool = False,
                     categories: dict[str, int] | None = None) -> tuple[ObjectObservation, ...]:
    """Parse one provider reply; malformed items are dropped, never accepted.

    A malformed reply envelope fails closed. A single malformed item used to
    discard every valid object in the same reply; it is now skipped and
    counted under a fixed reason in ``rejected`` (no content is retained).
    """
    try:
        result = json.loads(text)
        if not isinstance(result, dict) or set(result) != {"detections"}:
            raise ValueError
        entries = result["detections"]
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise ApiDetectionError("invalid_api_detection_response") from exc
    observations = []
    for item in entries:
        reason = None
        keys = set(item) if isinstance(item, dict) else None
        if keys != {"kind", "label", "score", "box"} and not (
                plates and keys == {"kind", "label", "score", "box", "plate"}):
            reason = "shape"
        elif "plate" in item and (item["kind"] != "vehicle"
                                  or item["plate"] is not None
                                  and not isinstance(item["plate"], str)):
            reason = "plate"
        else:
            kind, label, score, box = (item[name] for name in
                                       ("kind", "label", "score", "box"))
            if not isinstance(kind, str) or kind not in _LABELS:
                reason = "kind"
                if categories is not None:
                    key = "kind:" + rejected_category(kind)
                    categories[key] = categories.get(key, 0) + 1
            elif not isinstance(label, str) or label not in _LABELS[kind]:
                reason = "label:" + kind
                if categories is not None:
                    key = "label:" + rejected_category(label)
                    categories[key] = categories.get(key, 0) + 1
            elif type(score) not in (float, int):
                reason = "score"
            elif (not isinstance(box, list) or len(box) != 4
                    or any(type(point) not in (float, int) for point in box)):
                reason = "box"
            else:
                observation = ObjectObservation(kind, label, score, tuple(box),
                                                normalize_plate(item.get("plate")))
                try:
                    validate_observation(observation)
                except TrackingError:
                    reason = "score" if not 0 <= score <= 1 else "box"
                else:
                    if score >= threshold:
                        observations.append(observation)
        if reason is not None and rejected is not None:
            rejected[reason] = rejected.get(reason, 0) + 1
    return tuple(observations)


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


def _http_429_category(body: bytes) -> str:
    """"quota" or "rate" only when the provider's own codes agree; else "unknown"."""
    try:
        error = json.loads(body).get("error")
    except (AttributeError, UnicodeError, ValueError):
        return "unknown"
    if not isinstance(error, dict):
        return "unknown"
    found = set()
    for field in ("code", "type"):
        value = error.get(field)
        if isinstance(value, str) and value in _QUOTA_CODES:
            found.add("quota")
        elif isinstance(value, str) and value in _RATE_CODES:
            found.add("rate")
    return found.pop() if len(found) == 1 else "unknown"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _post(url: str, headers: dict, payload: dict, *, timeout: float = 15) -> dict:
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    request = Request(url, data=body, headers={**headers, "Content-Type": "application/json"},
                      method="POST")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
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
    except HTTPError as exc:
        code = ("api_detection_http_429" if exc.code == 429 else
                "api_detection_http_4xx" if 400 <= exc.code < 500 else
                "api_detection_http_5xx" if 500 <= exc.code < 600 else
                "api_detection_http_failure")
        error = ApiDetectionError(code)
        if exc.code == 429:
            try:
                body = exc.read(_MAX_ERROR_BYTES)
            except Exception:
                body = b""
            error.category = _http_429_category(body if isinstance(body, bytes) else b"")
        # Not chained: the HTTP error carries the provider's message and headers.
        raise error from None
    except URLError as exc:
        code = ("api_detection_dns_unavailable"
                if isinstance(exc.reason, socket.gaierror)
                else "api_detection_request_failed")
        raise ApiDetectionError(code) from exc
    except Exception as exc:
        raise ApiDetectionError("api_detection_request_failed") from exc


class ApiObjectDetector:
    """One paid request per motion-sampled frame.

    Requests are bounded by the motion gate (pairs, rearmed only after the
    scene quiets), the caller's single worker and a provider-wide failure
    backoff. A durable per-camera hourly request cap is an optional cost
    control and is off unless ``max_requests_per_hour`` is set.
    """

    def __init__(self, provider_config: dict[str, Any], state_dir: Path, *,
                 threshold: float, max_requests_per_hour: int | None = None,
                 transport: Callable[[str, dict, dict], dict] = _post,
                 package_lens_owned: Callable[[str], bool] | None = None,
                 plate_cameras: frozenset[str] = frozenset(),
                 fallback: dict[str, Any] | None = None,
                 fallback_transport: Callable[..., dict] | None = None,
                 clock: Callable[[], float] = time.time):
        if (type(threshold) not in (float, int) or not 0 < threshold <= 1
                or max_requests_per_hour is not None
                and (type(max_requests_per_hour) is not int
                     or not 2 <= max_requests_per_hour <= 3600)):
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
        # Room for the schema's largest valid reply, whatever was configured.
        if self.provider.max_output_tokens < _DETECTION_OUTPUT_TOKENS:
            self.provider.max_output_tokens = _DETECTION_OUTPUT_TOKENS
        self._failure_reasons: dict[str, dict[str, int]] = {}
        # Cameras whose own package lens owns Package: a main-lens "package"
        # is dropped before the close-up check, so no crop is uploaded.
        self._package_lens_owned = package_lens_owned or (lambda _camera: False)
        # Opt-in cameras whose vehicles also get plate text; counts only.
        self.plate_cameras = frozenset(plate_cameras)
        self._plate_counts: dict[str, dict[str, int]] = {}
        self.budget = (EventBudget(state_dir, limit=max_requests_per_hour,
                                   namespace="vision-request")
                       if max_requests_per_hour is not None else None)
        self.fresh_reserve = (max_requests_per_hour // _FRESH_RESERVE_DIVISOR
                              if max_requests_per_hour is not None else None)
        self.provider_failures = 0
        self.backoff_skips = 0
        self._consecutive_failures = 0
        self._provider_retry_at = 0.0
        # In memory only: a restart starts with no success time and no outage.
        self.clock = clock
        self.last_provider_success_at: int | None = None
        self.provider_outage_since: int | None = None
        self.http_429_categories = dict.fromkeys(HTTP_429_CATEGORIES, 0)
        self.current_429_category: str | None = None
        # Per camera, per minute: what the provider gate measured and decided (#6).
        self._gate_history: dict[str, MinuteHistory] = {}
        self.transport = transport
        self.motion = _MotionGate()
        self._camera_counts: dict[str, dict[str, int]] = {}
        self._kind_counts: dict[str, dict[str, dict[str, int]]] = {}
        self._profiles: dict[str, dict[str, int]] = {}
        self._package_checks: dict[str, dict[str, int]] = {}
        self._frame_width: dict[str, int] = {}
        self._budget_retry_at: dict[str, float] = {}
        self._remote_host = (endpoint.hostname if transport is _post
                             and self.provider.provider in {"openai", "anthropic"}
                             else None)
        self._dns_ok_until = 0.0
        self._dns_retry_at = 0.0
        # Optional local fallback (Ollama on the LAN), used only while the
        # primary provider fails; bounded per camera and hour, with its own
        # timeout, and never charged to the paid request budget.
        self.fallback = None
        self.fallback_counts = {"requests": 0, "objects": 0, "empty": 0, "failed": 0,
                                "rate_limited": 0,
                                "reasons": dict.fromkeys(FALLBACK_REASONS, 0)}
        self._fallback_times: dict[str, list[float]] = {}
        if fallback is not None:
            try:
                provider = validate_inference_config(dict(fallback["provider_config"]),
                                                     require_api_key=False)
            except (ProviderError, KeyError, TypeError) as exc:
                raise ApiDetectionError("invalid_api_detection_fallback") from exc
            host = urlsplit(provider.base_url).hostname or ""
            try:
                # RFC 1918 LAN or loopback only; "is_private" also admits
                # documentation and other reserved ranges.
                address = ipaddress.ip_address(host)
                private = any(address in ipaddress.ip_network(net) for net in (
                    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))
            except ValueError:
                private = False
            timeout, per_hour = fallback.get("timeout_s", 60), fallback.get("max_per_hour", 120)
            if (provider.provider != "ollama" or not private
                    or type(timeout) is not int or not 5 <= timeout <= 180
                    or type(per_hour) is not int or not 1 <= per_hour <= 3600):
                raise ApiDetectionError("invalid_api_detection_fallback")
            provider.max_output_tokens = max(provider.max_output_tokens, 512)
            self.fallback = provider
            self.fallback_timeout = timeout
            self.fallback_max_per_hour = per_hour
            self.fallback_transport = fallback_transport or (
                lambda url, headers, payload: _post(url, headers, payload, timeout=timeout))

    def _check_remote_dns(self) -> None:
        """Leave the paid-request allowance untouched while DNS is unavailable."""
        if self._remote_host is None:
            return
        now = time.monotonic()
        if now < self._dns_retry_at:
            raise ApiDetectionError("api_detection_dns_unavailable")
        if now < self._dns_ok_until:
            return
        try:
            addresses = socket.getaddrinfo(self._remote_host, 443,
                                           type=socket.SOCK_STREAM)
            if not addresses:
                raise socket.gaierror()
        except socket.gaierror as exc:
            self._dns_retry_at = now + _DNS_RETRY_SECONDS
            raise ApiDetectionError("api_detection_dns_unavailable") from exc
        self._dns_ok_until = now + _DNS_RETRY_SECONDS

    def detect_for_camera(self, camera_mac: str,
                          frame: bytes) -> tuple[ObjectObservation, ...]:
        if not isinstance(frame, bytes) or not 0 < len(frame) <= _MAX_FRAME_BYTES:
            raise ApiDetectionError("invalid_api_detection_frame")
        try:
            if image_mime(frame) != "image/jpeg":
                raise ApiDetectionError("invalid_api_detection_frame")
        except ProviderError as exc:
            raise ApiDetectionError("invalid_api_detection_frame") from exc
        if time.monotonic() < self._budget_retry_at.get(camera_mac, 0):
            return ()
        allow_startup_probe = (self.budget is None or self.motion.has_baseline(camera_mac)
                               or self.budget.remaining(camera_mac) == self.budget.limit)
        opened = self.motion.should_request(camera_mac, frame,
                                            allow_startup_probe=allow_startup_probe)
        gate = self._gate(camera_mac)
        gate.count("frames")
        changed = self.motion.last_changed.get(camera_mac)
        if changed is not None:
            gate.maximum("peak_cells", changed)
            gate.count("near_miss" if 2 * changed >= _MOTION_CHANGED_CELLS > changed
                       else "over_threshold" if changed >= _MOTION_CHANGED_CELLS else "quiet")
        if not opened:
            return ()
        gate.count("opened")
        fallback_reason = None
        if time.monotonic() < self._provider_retry_at:
            if self.fallback is None:
                # Drop this pair; the gate rearms on later motion after the pause.
                self.motion.defer(camera_mac)
                self.backoff_skips += 1
                gate.count("backoff_skipped")
                return ()
            fallback_reason = "backoff"
        if (self.budget is not None and self.motion.is_refresh(camera_mac)
                and self.budget.remaining(camera_mac) <= self.fresh_reserve):
            # Keep the last part of the unchanged hourly cap for motion that
            # starts in a quiet scene, e.g. an animal after people left.
            self.motion.defer(camera_mac)
            counts = self._camera_counts.setdefault(camera_mac, {
                "responses": 0, "empty_responses": 0,
                "below_threshold": 0, "accepted_objects": 0,
            })
            counts["refreshes_deferred"] = counts.get("refreshes_deferred", 0) + 1
            gate.count("deferred")
            return ()
        if fallback_reason is None:
            try:
                self._check_remote_dns()
            except ApiDetectionError:
                if self.fallback is None:
                    self.motion.reset(camera_mac)
                    raise
                fallback_reason = "dns_unavailable"
        if (fallback_reason is None and self.budget is not None
                and not self.budget.claim(camera_mac)):
            # Preserve the current scene. Otherwise a denied request resets
            # startup sampling and burns each newly freed allowance on an
            # idle frame, keeping a busy camera at zero budget indefinitely.
            self.motion.wait_for_motion(camera_mac)
            self._budget_retry_at[camera_mac] = time.monotonic() + 60
            gate.count("deferred")
            return ()
        try:
            plates = camera_mac in self.plate_cameras
            url, headers, payload = self.provider.build_request(
                [frame], _PROMPT + _PLATE_PROMPT if plates else _PROMPT)
            if self.provider.provider == "openai" and self.provider.model == "gpt-6-luna":
                payload["reasoning"] = {"effort": "none"}
            reply = None
            gate.count("requests")
            used_fallback = fallback_reason is not None
            if used_fallback:
                text = self._fallback_text(camera_mac, frame, fallback_reason)
                plates = False
            else:
                try:
                    reply = self.transport(url, headers, payload)
                    text = self.provider.parse_response(reply)
                except (ApiDetectionError, ProviderError, TypeError, ValueError) as exc:
                    gate.count("failed")
                    if isinstance(exc, ProviderError) and reply is not None:
                        self._count_failure(camera_mac, _envelope_failure(reply))
                    if not (isinstance(exc, ApiDetectionError)
                            and exc.args == ("api_detection_dns_unavailable",)):
                        self._provider_failed(exc)
                    reason = self._fallback_reason(exc)
                    if self.fallback is None or reason is None:
                        raise
                    text = self._fallback_text(camera_mac, frame, reason)
                    used_fallback, plates = True, False
                else:
                    self._provider_succeeded()
            # Keep only counts. This distinguishes a real empty provider
            # response from an object rejected by the configured score gate.
            rejected: dict[str, int] = {}
            categories: dict[str, int] = {}
            try:
                reported = parse_detections(text, threshold=0, rejected=rejected,
                                            plates=plates, categories=categories)
            except ApiDetectionError:
                self._count_failure(camera_mac, _parse_failure(text))
                raise
            accepted = tuple(item for item in reported
                             if item.score >= self.threshold)
            if used_fallback:
                self.fallback_counts["objects" if accepted else "empty"] += 1
            counts = self._camera_counts.setdefault(camera_mac, {
                "responses": 0, "empty_responses": 0,
                "below_threshold": 0, "accepted_objects": 0,
            })
            counts["responses"] += 1
            counts["empty_responses"] += not reported and not rejected
            outcome = ("objects" if accepted else "low" if reported or rejected
                       else "empty")
            gate.count(outcome)
            profile = self._profiles.setdefault(camera_mac, dict.fromkeys(_PROFILE_KEYS, 0))
            profile[f"{_frame_mode(frame)}_{outcome}"] += 1
            try:
                with Image.open(BytesIO(frame)) as sized:
                    self._frame_width[camera_mac] = sized.width
            except (OSError, ValueError, UnidentifiedImageError):
                pass
            counts["below_threshold"] += len(reported) - len(accepted)
            counts["accepted_objects"] += len(accepted)
            kinds = self._kind_counts.setdefault(camera_mac, {
                "below_threshold": dict.fromkeys(_LABELS, 0),
                "near_threshold": dict.fromkeys(_LABELS, 0),
                "rejected_items": dict.fromkeys(_ITEM_REJECTIONS, 0)})
            for item in reported:
                if item.score < self.threshold:
                    kinds["below_threshold"][item.kind] += 1
                    # Score evidence for the threshold without keeping scores.
                    kinds["near_threshold"][item.kind] += item.score >= 0.5
            for reason, count in rejected.items():
                kinds["rejected_items"][reason] += count
            buckets = kinds.setdefault("rejected_categories",
                                       dict.fromkeys(REJECTED_CATEGORY_KEYS, 0))
            for key, count in categories.items():
                buckets[key] += count
            if (any(item.kind == "package" for item in accepted)
                    and self._package_lens_owned(camera_mac)):
                checks = self._package_checks.setdefault(
                    camera_mac, dict.fromkeys(_PACKAGE_CHECK_KEYS, 0))
                checks["lens_owned"] += sum(item.kind == "package" for item in accepted)
                accepted = tuple(item for item in accepted if item.kind != "package")
            if any(item.kind == "package" for item in accepted):
                accepted = tuple(
                    verified for item in accepted
                    for verified in ((self._verify_package(camera_mac, frame, item),)
                                     if item.kind == "package" else (item,))
                    if verified is not None)
            if plates:
                read = self._plate_counts.setdefault(camera_mac, dict.fromkeys(
                    ("vehicles", "plates_read", "plates_partial"), 0))
                for item in accepted:
                    if item.kind == "vehicle":
                        read["vehicles"] += 1
                        read["plates_read"] += item.plate is not None
                        read["plates_partial"] += item.plate is not None and "?" in item.plate
            self.motion.sample_result(camera_mac, found_object=bool(accepted))
            return accepted
        except ApiDetectionError as exc:
            self.motion.sample_result(camera_mac, found_object=False)
            if exc.args == ("api_detection_dns_unavailable",):
                self._dns_ok_until = 0.0
                self._dns_retry_at = time.monotonic() + _DNS_RETRY_SECONDS
            raise
        except ProviderError as exc:
            self.motion.sample_result(camera_mac, found_object=False)
            raise ApiDetectionError("api_detection_provider_response_invalid") from exc
        except (TypeError, ValueError) as exc:
            self.motion.sample_result(camera_mac, found_object=False)
            raise ApiDetectionError("api_detection_request_failed") from exc

    def _fallback_text(self, camera_mac: str, frame: bytes, reason: str) -> str:
        """One bounded local request; the reply in the shared schema, or a fixed error."""
        now = time.monotonic()
        recent = [t for t in self._fallback_times.get(camera_mac, ()) if now - t < 3600]
        if len(recent) >= self.fallback_max_per_hour:
            self._fallback_times[camera_mac] = recent
            self.fallback_counts["rate_limited"] += 1
            raise ApiDetectionError("api_detection_fallback_rate_limited")
        self._fallback_times[camera_mac] = recent + [now]
        self.fallback_counts["requests"] += 1
        self.fallback_counts["reasons"][reason] += 1
        try:
            url, headers, payload = self.fallback.build_request([frame], _FALLBACK_PROMPT)
            payload["format"] = _FALLBACK_SCHEMA
            text = fallback_to_fractions(
                self.fallback.parse_response(self.fallback_transport(url, headers, payload)))
        except (ApiDetectionError, ProviderError, TypeError, ValueError) as exc:
            self.fallback_counts["failed"] += 1
            raise ApiDetectionError("api_detection_fallback_failed") from exc
        return text

    @staticmethod
    def _fallback_reason(exc: Exception) -> str | None:
        code = exc.args[0] if isinstance(exc, ApiDetectionError) and exc.args else None
        return {"api_detection_http_429": "http_429", "api_detection_http_5xx": "http_5xx",
                "api_detection_request_failed": "request_failed",
                "api_detection_dns_unavailable": "dns_unavailable"}.get(code)

    def _gate(self, camera_mac: str) -> MinuteHistory:
        """Per-minute gate counters for one camera: counts and cell maxima only."""
        history = self._gate_history.get(camera_mac)
        if history is None:
            history = self._gate_history[camera_mac] = MinuteHistory(
                ("frames", "quiet", "near_miss", "over_threshold", "opened", "backoff_skipped",
                 "deferred", "requests", "empty", "low", "objects", "failed"),
                maxima=("peak_cells",), latest=("threshold_cells",), clock=self.clock)
        history.set("threshold_cells", _MOTION_CHANGED_CELLS)
        return history

    def gate_history(self, camera_mac: str) -> list[dict[str, int | None]]:
        history = self._gate_history.get(camera_mac)
        return history.snapshot() if history is not None else []

    def _count_failure(self, camera_mac: str, reason: str) -> None:
        counts = self._failure_reasons.setdefault(camera_mac, dict.fromkeys(_FAILURE_REASONS, 0))
        counts[reason] = counts.get(reason, 0) + 1

    def _verify_package(self, camera_mac: str, frame: bytes,
                        item: ObjectObservation) -> ObjectObservation | None:
        """Second-stage check of one package on a close-up crop.

        A cat sitting in Flur's night-IR frame was reported as a package, even
        with pet guidance in the prompt. A package is kept only when a crop of
        its box is again classified as a package; a pet becomes an animal and
        anything else, or a failed check, is dropped rather than published.
        """
        result = self._package_checks.setdefault(
            camera_mac, dict.fromkeys(_PACKAGE_CHECK_KEYS, 0))
        try:
            with Image.open(BytesIO(frame)) as image:
                width, height = image.size
                x1, y1, x2, y2 = item.box
                margin_x, margin_y = (x2 - x1) * 0.25, (y2 - y1) * 0.25
                left = max(0, int((x1 - margin_x) * width))
                top = max(0, int((y1 - margin_y) * height))
                right = min(width, max(left + 16, int((x2 + margin_x) * width)))
                bottom = min(height, max(top + 16, int((y2 + margin_y) * height)))
                crop = image.convert("RGB").crop((left, top, right, bottom))
            scale = _VERIFY_SIDE / max(crop.size)
            crop = crop.resize((max(1, round(crop.width * scale)),
                                max(1, round(crop.height * scale))))
            out = BytesIO()
            crop.save(out, format="JPEG", quality=90)
            url, headers, payload = self.provider.build_request(
                [out.getvalue()], _VERIFY_PROMPT)
            if self.provider.provider == "openai" and self.provider.model == "gpt-6-luna":
                payload["reasoning"] = {"effort": "none"}
            answer = json.loads(self.provider.parse_response(
                self.transport(url, headers, payload)))
            if (not isinstance(answer, dict) or set(answer) != {"kind", "label"}
                    or answer != {"kind": "none", "label": "none"}
                    and (answer["kind"] not in _LABELS
                         or answer["label"] not in _LABELS[answer["kind"]])):
                raise ValueError
        except (ApiDetectionError, ProviderError, OSError, TypeError, ValueError,
                UnidentifiedImageError):
            result["failed"] += 1
            return None
        if answer["kind"] == "package":
            result["confirmed"] += 1
            return item
        if answer["kind"] == "animal":
            result["relabelled_animal"] += 1
            return ObjectObservation("animal", answer["label"], item.score, item.box)
        result["rejected"] += 1
        return None

    def _provider_succeeded(self) -> None:
        """A parsed provider reply ends the current outage."""
        self._consecutive_failures = 0
        self.provider_outage_since = None
        self.current_429_category = None
        self.last_provider_success_at = int(self.clock())

    def provider_status(self) -> dict[str, object]:
        """Times and fixed codes only; never a provider message or payload."""
        return {
            "api_last_provider_success_at": self.last_provider_success_at,
            "api_provider_outage_since": self.provider_outage_since,
            "api_consecutive_provider_failures": self._consecutive_failures,
            "api_http_429_categories": dict(self.http_429_categories),
            "api_current_429_category": self.current_429_category,
            **({"api_fallback": {key: (dict(value) if isinstance(value, dict) else value)
                                 for key, value in self.fallback_counts.items()}}
               if self.fallback is not None else {}),
        }

    def _provider_failed(self, exc: Exception | None = None) -> None:
        if self.provider_outage_since is None:
            self.provider_outage_since = int(self.clock())
        if isinstance(exc, ApiDetectionError) and exc.args == ("api_detection_http_429",):
            category = getattr(exc, "category", "unknown")
            if category not in self.http_429_categories:
                category = "unknown"
            self.http_429_categories[category] += 1
            self.current_429_category = category
        else:
            self.current_429_category = None
        self.provider_failures += 1
        self._consecutive_failures += 1
        delay = min(_BACKOFF_MAX_SECONDS,
                    _BACKOFF_FIRST_SECONDS * 2 ** (self._consecutive_failures - 1))
        self._provider_retry_at = time.monotonic() + delay

    def skip_confirmation(self, camera_mac: str) -> None:
        """Every sampled object is already confirmed: keep the request."""
        if self.motion.cancel_confirmation(camera_mac):
            counts = self._camera_counts.setdefault(camera_mac, {
                "responses": 0, "empty_responses": 0,
                "below_threshold": 0, "accepted_objects": 0,
            })
            counts["confirmations_saved"] = counts.get("confirmations_saved", 0) + 1

    def diagnostic_counts(self, camera_mac: str) -> dict[str, object]:
        """Return content-free response counts for one configured camera."""
        result: dict[str, object] = dict(self._camera_counts.get(camera_mac, {
            "responses": 0, "empty_responses": 0,
            "below_threshold": 0, "accepted_objects": 0,
        }))
        kinds = self._kind_counts.get(camera_mac, {
            "below_threshold": dict.fromkeys(_LABELS, 0),
            "near_threshold": dict.fromkeys(_LABELS, 0),
            "rejected_items": dict.fromkeys(_ITEM_REJECTIONS, 0)})
        result["below_threshold_by_kind"] = dict(kinds["below_threshold"])
        # Of those, how many scored at least 0.5 (between 0.5 and the threshold).
        result["near_threshold_by_kind"] = dict(kinds["near_threshold"])
        result["rejected_items"] = dict(kinds["rejected_items"])
        result["rejected_categories"] = {k: v for k, v in kinds.get(
            "rejected_categories", {}).items() if v}
        result["request_profile"] = dict(self._profiles.get(
            camera_mac, dict.fromkeys(_PROFILE_KEYS, 0)))
        result["last_frame_width"] = self._frame_width.get(camera_mac)
        result["failure_reasons"] = {k: v for k, v in self._failure_reasons.get(camera_mac, {}).items() if v}
        result["output_token_budget"] = self.provider.max_output_tokens
        result["package_checks"] = dict(self._package_checks.get(
            camera_mac, dict.fromkeys(_PACKAGE_CHECK_KEYS, 0)))
        if camera_mac in self.plate_cameras:
            result["plates"] = dict(self._plate_counts.get(camera_mac, dict.fromkeys(
                ("vehicles", "plates_read", "plates_partial"), 0)))
        return result

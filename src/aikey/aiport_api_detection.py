"""Bounded API object observations for the AI Port camera engine.

This is an optional detector. API-reported scores and boxes are uncalibrated
until measured against camera footage; configuration alone is not parity proof.
"""

from __future__ import annotations

import json
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
from .aiport_event_budget import EventBudget
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


_ITEM_REJECTIONS = ("shape", "kind", "label:person", "label:vehicle", "label:animal",
                    "label:package", "score", "box")


def parse_detections(text: str, *, threshold: float,
                     rejected: dict[str, int] | None = None
                     ) -> tuple[ObjectObservation, ...]:
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
        if not isinstance(item, dict) or set(item) != {"kind", "label", "score", "box"}:
            reason = "shape"
        else:
            kind, label, score, box = (item[name] for name in
                                       ("kind", "label", "score", "box"))
            if not isinstance(kind, str) or kind not in _LABELS:
                reason = "kind"
            elif not isinstance(label, str) or label not in _LABELS[kind]:
                reason = "label:" + kind
            elif type(score) not in (float, int):
                reason = "score"
            elif (not isinstance(box, list) or len(box) != 4
                    or any(type(point) not in (float, int) for point in box)):
                reason = "box"
            else:
                observation = ObjectObservation(kind, label, score, tuple(box))
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
    except HTTPError as exc:
        code = ("api_detection_http_429" if exc.code == 429 else
                "api_detection_http_4xx" if 400 <= exc.code < 500 else
                "api_detection_http_5xx" if 500 <= exc.code < 600 else
                "api_detection_http_failure")
        raise ApiDetectionError(code) from exc
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
                 transport: Callable[[str, dict, dict], dict] = _post):
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
        self.budget = (EventBudget(state_dir, limit=max_requests_per_hour,
                                   namespace="vision-request")
                       if max_requests_per_hour is not None else None)
        self.fresh_reserve = (max_requests_per_hour // _FRESH_RESERVE_DIVISOR
                              if max_requests_per_hour is not None else None)
        self.provider_failures = 0
        self.backoff_skips = 0
        self._consecutive_failures = 0
        self._provider_retry_at = 0.0
        self.transport = transport
        self.motion = _MotionGate()
        self._camera_counts: dict[str, dict[str, int]] = {}
        self._kind_counts: dict[str, dict[str, dict[str, int]]] = {}
        self._profiles: dict[str, dict[str, int]] = {}
        self._frame_width: dict[str, int] = {}
        self._budget_retry_at: dict[str, float] = {}
        self._remote_host = (endpoint.hostname if transport is _post
                             and self.provider.provider in {"openai", "anthropic"}
                             else None)
        self._dns_ok_until = 0.0
        self._dns_retry_at = 0.0

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
        if not self.motion.should_request(camera_mac, frame,
                                          allow_startup_probe=allow_startup_probe):
            return ()
        if time.monotonic() < self._provider_retry_at:
            # Drop this pair; the gate rearms on later motion after the pause.
            self.motion.defer(camera_mac)
            self.backoff_skips += 1
            return ()
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
            return ()
        try:
            self._check_remote_dns()
        except ApiDetectionError:
            self.motion.reset(camera_mac)
            raise
        if self.budget is not None and not self.budget.claim(camera_mac):
            # Preserve the current scene. Otherwise a denied request resets
            # startup sampling and burns each newly freed allowance on an
            # idle frame, keeping a busy camera at zero budget indefinitely.
            self.motion.wait_for_motion(camera_mac)
            self._budget_retry_at[camera_mac] = time.monotonic() + 60
            return ()
        try:
            url, headers, payload = self.provider.build_request([frame], _PROMPT)
            if self.provider.provider == "openai" and self.provider.model == "gpt-6-luna":
                payload["reasoning"] = {"effort": "none"}
            try:
                reply = self.transport(url, headers, payload)
                text = self.provider.parse_response(reply)
            except (ApiDetectionError, ProviderError, TypeError, ValueError) as exc:
                if not (isinstance(exc, ApiDetectionError)
                        and exc.args == ("api_detection_dns_unavailable",)):
                    self._provider_failed()
                raise
            self._consecutive_failures = 0
            # Keep only counts. This distinguishes a real empty provider
            # response from an object rejected by the configured score gate.
            rejected: dict[str, int] = {}
            reported = parse_detections(text, threshold=0, rejected=rejected)
            accepted = tuple(item for item in reported
                             if item.score >= self.threshold)
            counts = self._camera_counts.setdefault(camera_mac, {
                "responses": 0, "empty_responses": 0,
                "below_threshold": 0, "accepted_objects": 0,
            })
            counts["responses"] += 1
            counts["empty_responses"] += not reported and not rejected
            outcome = ("objects" if accepted else "low" if reported or rejected
                       else "empty")
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
                "rejected_items": dict.fromkeys(_ITEM_REJECTIONS, 0)})
            for item in reported:
                if item.score < self.threshold:
                    kinds["below_threshold"][item.kind] += 1
            for reason, count in rejected.items():
                kinds["rejected_items"][reason] += count
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

    def _provider_failed(self) -> None:
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
            "rejected_items": dict.fromkeys(_ITEM_REJECTIONS, 0)})
        result["below_threshold_by_kind"] = dict(kinds["below_threshold"])
        result["rejected_items"] = dict(kinds["rejected_items"])
        result["request_profile"] = dict(self._profiles.get(
            camera_mac, dict.fromkeys(_PROFILE_KEYS, 0)))
        result["last_frame_width"] = self._frame_width.get(camera_mac)
        return result

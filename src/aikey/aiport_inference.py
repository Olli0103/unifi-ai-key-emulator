"""Bounded, fair inference for an explicitly allowlisted AI Port camera pool.

This schedules model calls; it does not enable a stream or publish a Protect
event. A caller supplies a pinned local model and an event-candidate observer.
No frame, result, camera identity, or model exception is persisted or logged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from .aiport_detection import ObjectObservation
from .aiport_ingest import IngressError, normalize_mac
from .aiport_tracking import TrackingError, validate_observation


class Detector(Protocol):
    def detect(self, frame: bytes) -> tuple[ObjectObservation, ...]: ...


class FairInference:
    """Use one model across at most five cameras, one pending frame each.

    A camera cannot occupy more than every other turn when another camera has
    a pending frame. New frames replace old pending frames, never queue behind
    them. An inference or observer failure disables only that camera.
    """

    def __init__(self, camera_macs: list[str], *,
                 load_detector: Callable[[], Detector],
                 on_result: Callable[[str, tuple[ObjectObservation, ...], int], Awaitable[None]],
                 on_unavailable: Callable[[str], Awaitable[None]] | None = None,
                 max_frames_per_camera: int):
        if (not isinstance(camera_macs, list) or not 1 <= len(camera_macs) <= 5
                or type(max_frames_per_camera) is not int
                or not 1 <= max_frames_per_camera <= 120
                or not callable(load_detector) or not callable(on_result)
                or on_unavailable is not None and not callable(on_unavailable)):
            raise IngressError("invalid_inference_policy")
        cameras = tuple(normalize_mac(camera) for camera in camera_macs)
        if len(set(cameras)) != len(cameras):
            raise IngressError("duplicate_camera")
        self._cameras = cameras
        self._allowed = frozenset(cameras)
        self._load_detector = load_detector
        self._on_result = on_result
        self._on_unavailable = on_unavailable
        self._max_frames = max_frames_per_camera
        self._model: Detector | None = None
        self._pending: dict[str, tuple[bytes, int]] = {}
        self._disabled: set[str] = set()
        self._attempts = dict.fromkeys(cameras, 0)
        self._successes = dict.fromkeys(cameras, 0)
        self._last_index = -1
        self._worker: asyncio.Task | None = None
        self._closed = False
        self._global_failure = False
        self.dropped_frames = 0
        self.failed_cameras = 0

    async def observe(self, camera_mac: str, frame: bytes, *, generation: int) -> None:
        camera = normalize_mac(camera_mac)
        if camera not in self._allowed:
            raise IngressError("camera_not_authorized")
        if type(frame) is not bytes or not 4 <= len(frame) <= 1024 * 1024:
            raise IngressError("invalid_decoded_frame")
        if type(generation) is not int or generation < 0:
            raise IngressError("invalid_policy_generation")
        if (self._closed or self._global_failure or camera in self._disabled
                or self._attempts[camera] >= self._max_frames):
            self.dropped_frames += 1
            return
        if camera in self._pending:
            self.dropped_frames += 1
        self._pending[camera] = (frame, generation)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="aiport-fair-inference")

    def _next_camera(self) -> str:
        for offset in range(1, len(self._cameras) + 1):
            index = (self._last_index + offset) % len(self._cameras)
            camera = self._cameras[index]
            if camera in self._pending:
                self._last_index = index
                return camera
        raise RuntimeError("inference queue is empty")

    async def _run(self) -> None:
        while self._pending and not self._closed and not self._global_failure:
            camera = self._next_camera()
            frame, generation = self._pending.pop(camera)
            self._attempts[camera] += 1
            try:
                if self._model is None:
                    try:
                        self._model = await asyncio.to_thread(self._load_detector)
                        if (self._model is None
                                or not callable(getattr(self._model, "detect", None))):
                            raise TypeError("invalid detector")
                    except Exception:
                        self._global_failure = True
                        self._pending.clear()
                        for unavailable_camera in self._cameras:
                            await self._notify_unavailable(unavailable_camera)
                        return
                result = await asyncio.to_thread(self._model.detect, frame)
                if not isinstance(result, tuple) or len(result) > 100:
                    raise TrackingError("invalid_tracking_observation")
                for observation in result:
                    validate_observation(observation)
                if self._closed:
                    return
                await self._on_result(camera, result, generation)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep model, exception text and private camera identity out
                # of public health. One bad camera must not stop the others.
                self._disabled.add(camera)
                self._pending.pop(camera, None)
                self.failed_cameras += 1
                await self._notify_unavailable(camera)
            else:
                self._successes[camera] += 1
                if self._attempts[camera] >= self._max_frames:
                    self._pending.pop(camera, None)
                    await self._notify_unavailable(camera)

    async def _notify_unavailable(self, camera: str) -> None:
        if self._on_unavailable is not None and not self._closed:
            try:
                await self._on_unavailable(camera)
            except Exception:
                # A status transport failure must not expose payloads or
                # make another camera's inference unavailable.
                pass

    async def join(self) -> None:
        """Wait for the currently queued diagnostic work to finish."""
        worker = self._worker
        if worker is not None:
            await worker

    def discard_pending(self, camera_mac: str) -> None:
        """Forget queued frames for a camera whose stream or policy changed."""
        camera = normalize_mac(camera_mac)
        if camera not in self._allowed:
            raise IngressError("camera_not_authorized")
        self._pending.pop(camera, None)

    def is_available(self, camera_mac: str) -> bool:
        """Whether this camera can still receive a model call in this permit."""
        camera = normalize_mac(camera_mac)
        if camera not in self._allowed:
            raise IngressError("camera_not_authorized")
        return (not self._closed and not self._global_failure
                and camera not in self._disabled
                and self._attempts[camera] < self._max_frames)

    async def close(self) -> None:
        self._closed = True
        self._pending.clear()
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._model = None

    def snapshot(self) -> dict[str, int | bool]:
        """Aggregate, content-free counters safe for a private health page."""
        return {
            "camera_count": len(self._cameras),
            "attempts": sum(self._attempts.values()),
            "successes": sum(self._successes.values()),
            "dropped_frames": self.dropped_frames,
            "failed_cameras": self.failed_cameras,
            "pending_cameras": len(self._pending),
            "model_load_failed": self._global_failure,
            "closed": self._closed,
        }

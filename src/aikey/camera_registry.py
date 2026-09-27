"""Fresh, read-only Protect inventory as a fail-closed admission boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Callable

# Why a camera cannot receive automatic captions (#9). Health reports only
# counts per reason; per-camera answers stay on the authenticated admin side.
REASONS = {
    "offline": "Camera is not connected to Protect.",
    "legacy_ingress_needed": "No onboard smart detections; an AI Port or verified ingress path is needed.",
    "model_not_allowed": "Camera model is not in the continuous camera_models policy.",
    "not_in_inventory": "Camera is not in the current Protect inventory.",
    "inventory_stale": "The Protect inventory is stale or unavailable; admission fails closed.",
}

from .camera_inventory import InventoryError, fetch_inventory


class CameraRegistry:
    """Allow connected smart-event cameras only while a pinned read is fresh."""

    def __init__(self, host: str, options: dict, *, clock: Callable[[], float] = time.monotonic):
        self.host = host
        self.api_key_file = Path(options["api_key_file"])
        self.trust_file = Path(options["web_trust_file"])
        self.cert_file = Path(options["web_cert_file"])
        self.camera_models = frozenset(options["camera_models"])
        self.refresh_seconds = options["refresh_seconds"]
        self.clock = clock
        self._allowed: frozenset[str] = frozenset()
        self._excluded: dict[str, str] = {}
        self._fetched_at: float | None = None
        self._task: asyncio.Task | None = None
        self._last_error: str | None = None

    @property
    def allowed_ids(self) -> frozenset[str]:
        if not self._fresh:
            return frozenset()
        return self._allowed

    @property
    def _fresh(self) -> bool:
        return (self._fetched_at is not None
                and 0 <= self.clock() - self._fetched_at < 2 * self.refresh_seconds)

    def allows(self, camera_id: str) -> bool:
        return isinstance(camera_id, str) and camera_id in self.allowed_ids

    def eligibility(self, camera_id: str) -> dict:
        """Per-feature eligibility for one camera, with the reason it cannot run."""
        if not self._fresh:
            reason = "inventory_stale"
        elif camera_id in self._allowed:
            return {"caption": {"eligible": True, "reason": None}}
        else:
            reason = self._excluded.get(camera_id, "not_in_inventory")
        return {"caption": {"eligible": False, "reason": reason, "detail": REASONS[reason]}}

    def status(self) -> dict:
        excluded: dict[str, int] = {}
        if self._fresh:
            for reason in self._excluded.values():
                excluded[reason] = excluded.get(reason, 0) + 1
        return {"fresh": self._fresh, "eligible_cameras": len(self.allowed_ids),
                "caption_ineligible": dict(sorted(excluded.items())),
                "last_error": self._last_error}

    def _reason(self, camera: dict) -> str | None:
        if camera["state"] != "CONNECTED":
            return "offline"
        if camera["processing_class"] != "smart_event_candidate":
            return "legacy_ingress_needed"
        if camera["model"] not in self.camera_models:
            return "model_not_allowed"
        return None

    async def refresh_once(self) -> None:
        try:
            report = await fetch_inventory(
                self.host, api_key_file=self.api_key_file,
                trust_file=self.trust_file, cert_file=self.cert_file,
            )
            reasons = {camera["id"]: self._reason(camera) for camera in report["cameras"]}
            allowed = frozenset(camera_id for camera_id, reason in reasons.items() if reason is None)
            excluded = {camera_id: reason for camera_id, reason in reasons.items() if reason}
        except (InventoryError, KeyError, TypeError, ValueError) as exc:
            self._allowed = frozenset()
            self._excluded = {}
            self._fetched_at = None
            self._last_error = type(exc).__name__
            return
        self._allowed = allowed
        self._excluded = excluded
        self._fetched_at = self.clock()
        self._last_error = None

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.refresh_once()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_seconds)
            await self.refresh_once()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._allowed = frozenset()
        self._excluded = {}
        self._fetched_at = None

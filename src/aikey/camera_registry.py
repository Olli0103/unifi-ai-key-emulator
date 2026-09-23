"""Fresh, read-only Protect inventory as a fail-closed admission boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Callable

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

    def status(self) -> dict:
        return {"fresh": self._fresh, "eligible_cameras": len(self.allowed_ids),
                "last_error": self._last_error}

    async def refresh_once(self) -> None:
        try:
            report = await fetch_inventory(
                self.host, api_key_file=self.api_key_file,
                trust_file=self.trust_file, cert_file=self.cert_file,
            )
            allowed = frozenset(camera["id"] for camera in report["cameras"]
                                if camera["processing_class"] == "smart_event_candidate"
                                and camera["state"] == "CONNECTED"
                                and camera["model"] in self.camera_models)
        except (InventoryError, KeyError, TypeError, ValueError) as exc:
            self._allowed = frozenset()
            self._fetched_at = None
            self._last_error = type(exc).__name__
            return
        self._allowed = allowed
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
        self._fetched_at = None

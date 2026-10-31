"""Bounded single-flight sampling; health callers cannot queue filesystem reads."""

import asyncio
from copy import deepcopy
import time


class CaptionBudgetHealth:
    CACHE_SECONDS = 2.0
    WAIT_SECONDS = 0.15

    def __init__(self, budget):
        self.budget = budget
        self.task = None
        self.expires = 0.0
        self.value = {"observation": "unavailable"}

    def status(self):
        if self.expires > time.monotonic():
            return deepcopy(self.value)
        return {"observation": "unavailable"}

    async def refresh(self):
        if self.expires > time.monotonic():
            return
        if self.task is None:
            self.started = time.monotonic()
            self.task = asyncio.create_task(asyncio.to_thread(self.budget.snapshot))
            self.task.add_done_callback(self._completed)
        # Expiry, cancellation or a slow filesystem never opens another lane.
        try:
            await asyncio.wait_for(asyncio.shield(self.task), self.WAIT_SECONDS)
        except Exception:
            # No raw filesystem diagnostics, paths or journal records in health.
            pass

    def _completed(self, task):
        try:
            self.value = task.result()
            if time.monotonic() >= self.started + self.CACHE_SECONDS:
                self.value = {"observation": "unavailable"}
        except (Exception, asyncio.CancelledError):
            self.value = {"observation": "unavailable"}
        self.expires = (self.started + self.CACHE_SECONDS if self.value["observation"] == "available"
                        else time.monotonic() + self.CACHE_SECONDS)
        self.task = None

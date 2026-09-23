"""A shared detector gives each allowlisted camera a bounded fair turn."""

import asyncio
import threading

import pytest

from aikey.aiport_detection import ObjectObservation
from aikey.aiport_inference import FairInference
from aikey.aiport_ingest import IngressError


FIRST = "2A1122334455"
SECOND = "2A1122334456"
THIRD = "2A1122334457"


class Detector:
    def __init__(self, *, started=None, release=None):
        self.started = started
        self.release = release
        self.calls = []

    def detect(self, frame):
        self.calls.append(frame)
        if self.started is not None and len(self.calls) == 1:
            self.started.set()
            assert self.release.wait(2)
        if frame == b"bad!":
            raise RuntimeError("private frame detail must be hidden")
        return (ObjectObservation("person", "person", 0.9,
                                  (0.1, 0.1, 0.4, 0.7)),)


@pytest.mark.asyncio
async def test_round_robin_coalesces_busy_camera_and_keeps_results_separate():
    started = threading.Event()
    release = threading.Event()
    model = Detector(started=started, release=release)
    results = []

    async def on_result(camera, observations, generation):
        results.append((camera, observations))

    scheduler = FairInference([FIRST, SECOND, THIRD], load_detector=lambda: model,
                              on_result=on_result, max_frames_per_camera=3)
    await scheduler.observe(FIRST, b"first", generation=1)
    assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
    await scheduler.observe(FIRST, b"stale", generation=1)
    await scheduler.observe(FIRST, b"latest", generation=1)
    await scheduler.observe(SECOND, b"second", generation=2)
    await scheduler.observe(THIRD, b"third", generation=3)
    release.set()
    await asyncio.wait_for(scheduler.join(), 3)
    assert [camera for camera, _ in results] == [FIRST, SECOND, THIRD, FIRST]
    assert model.calls == [b"first", b"second", b"third", b"latest"]
    assert scheduler.snapshot() == {
        "camera_count": 3, "attempts": 4, "successes": 4,
        "dropped_frames": 1, "failed_cameras": 0,
        "pending_cameras": 0, "model_load_failed": False, "closed": False,
    }
    await scheduler.close()


@pytest.mark.asyncio
async def test_one_inference_failure_disables_only_that_camera():
    results = []
    unavailable = []

    async def on_result(camera, observations, generation):
        results.append(camera)

    async def on_unavailable(camera):
        unavailable.append(camera)

    scheduler = FairInference([FIRST, SECOND], load_detector=Detector,
                              on_result=on_result, on_unavailable=on_unavailable,
                              max_frames_per_camera=2)
    await scheduler.observe(FIRST, b"bad!", generation=1)
    await scheduler.join()
    await scheduler.observe(FIRST, b"later", generation=1)
    await scheduler.observe(SECOND, b"okay", generation=1)
    await scheduler.join()
    assert results == [SECOND]
    assert unavailable == [FIRST]
    assert not scheduler.is_available(FIRST)
    assert scheduler.is_available(SECOND)
    assert scheduler.snapshot()["failed_cameras"] == 1
    assert scheduler.snapshot()["dropped_frames"] == 1
    await scheduler.close()


@pytest.mark.asyncio
async def test_loader_failure_stops_all_inference_without_exposing_exception():
    loads = 0
    unavailable = []

    def loader():
        nonlocal loads
        loads += 1
        raise RuntimeError("private model path")

    async def on_result(camera, observations, generation):
        raise AssertionError("must not receive a result")

    async def on_unavailable(camera):
        unavailable.append(camera)

    scheduler = FairInference([FIRST], load_detector=loader,
                              on_result=on_result, on_unavailable=on_unavailable,
                              max_frames_per_camera=1)
    await scheduler.observe(FIRST, b"frame", generation=1)
    await scheduler.join()
    await scheduler.observe(FIRST, b"later", generation=1)
    assert loads == 1
    assert unavailable == [FIRST]
    assert not scheduler.is_available(FIRST)
    assert scheduler.snapshot()["model_load_failed"] is True
    await scheduler.close()


@pytest.mark.asyncio
async def test_unlisted_camera_and_unbounded_frame_are_rejected():
    async def on_result(camera, observations, generation):
        raise AssertionError("must not receive a result")

    scheduler = FairInference([FIRST], load_detector=Detector,
                              on_result=on_result, max_frames_per_camera=1)
    with pytest.raises(IngressError, match="camera_not_authorized"):
        await scheduler.observe(SECOND, b"frame", generation=1)
    with pytest.raises(IngressError, match="invalid_decoded_frame"):
        await scheduler.observe(FIRST, b"x" * (1024 * 1024 + 1), generation=1)
    await scheduler.close()


@pytest.mark.asyncio
async def test_camera_reports_unavailable_when_its_frame_quota_is_consumed():
    unavailable = []

    async def on_result(camera, observations, generation):
        pass

    async def on_unavailable(camera):
        unavailable.append(camera)

    scheduler = FairInference([FIRST, SECOND], load_detector=Detector,
                              on_result=on_result, on_unavailable=on_unavailable,
                              max_frames_per_camera=1)
    await scheduler.observe(FIRST, b"frame", generation=1)
    await scheduler.join()
    assert unavailable == [FIRST]
    assert not scheduler.is_available(FIRST)
    assert scheduler.is_available(SECOND)
    await scheduler.close()

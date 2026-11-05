"""Decoded-frame time survives observer queues and slow model completion."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from aikey.aiport_ingest import _Session, StreamSpec, IngressError
from aikey.aiport_inference import FairInference
from aikey.aiport_candidate import CandidateService
from aikey.aiport_camera_engine import CameraEventCandidate
from test_aiport_faces import pool_config, change, jpeg, Sink, CAMERA


@pytest.mark.asyncio
async def test_decoder_stamps_before_observer_wait(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('aikey.aiport_ingest.time.time', lambda: clock[0])
    seen = []
    async def observe(frame, *, captured_ms, captured_monotonic_ms):
        assert captured_monotonic_ms > 0
        seen.append((frame, captured_ms))
    session = _Session(StreamSpec(CAMERA, '192.168.10.1', 'test', 640, 360, 10, 2),
                       '/not-run', observe, timed_frame_observer=True)
    reader = asyncio.StreamReader()
    reader.feed_data(b'\xff\xd8first\xff\xd9')
    reader.feed_eof()
    session.process = SimpleNamespace(stdout=reader)
    await session._read_frames()
    clock[0] = 140.0
    task = asyncio.create_task(session._observe_frames())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert seen == [(b'\xff\xd8first\xff\xd9', 100_000)]


@pytest.mark.asyncio
@pytest.mark.parametrize('preserve', [False, True])
async def test_model_queue_keeps_each_frames_clock(preserve):
    started, release = threading.Event(), threading.Event()
    seen = []
    class Detector:
        def detect(self, frame):
            if frame == b'first':
                started.set()
                assert release.wait(3)
            return ()
    async def result(camera, objects, generation, frame, *, captured_ms):
        seen.append((frame, captured_ms))
    worker = FairInference([CAMERA], load_detector=Detector, on_result=result,
                           max_frames_per_camera=None, preserve_first_pending=preserve)
    try:
        await worker.observe(CAMERA, b'first', generation=1, captured_ms=100_000)
        assert await asyncio.to_thread(started.wait, 2)
        await worker.observe(CAMERA, b'second', generation=1, captured_ms=101_000)
        await worker.observe(CAMERA, b'newest', generation=1, captured_ms=102_000)
        release.set()
        await worker.join()
        assert seen == ([(b'first',100_000),(b'second',101_000),(b'newest',102_000)]
                        if preserve else [(b'first',100_000),(b'newest',102_000)])
        with pytest.raises(IngressError, match='invalid_frame_timestamp'):
            await worker.observe(CAMERA, b'invalid', generation=1, captured_ms=True)
    finally:
        release.set()
        await worker.close()


@pytest.mark.asyncio
async def test_published_snapshot_and_enter_use_frame_not_completion_clock(tmp_path):
    service = CandidateService(pool_config(tmp_path), tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{'deviceID': CAMERA}]
    sink = Sink()
    service._current_ws = sink
    try:
        await service._publish_pool_candidates(
            (CameraEventCandidate(CAMERA, change('enter'), (1,)),),
            frame=jpeg(), captured_ms=100_000, captured_monotonic_ms=50_000)
        await service._publish_pool_candidates(
            (CameraEventCandidate(CAMERA, change('leave'), (1,)),))
        enter, leave = sink.events('EventSmartDetect')
        assert enter['clockWall'] == 100_000
        assert leave['clockWall'] > 100_000
        assert leave['smartDetectSnapshots'][0]['clockBestWall'] == 100_000
        assert leave['smartDetectSnapshots'][0]['clockBestMonotonic'] == 50_000
    finally:
        await service.stop()

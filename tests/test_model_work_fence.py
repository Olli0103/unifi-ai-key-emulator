"""Kernel reader/writer ownership, including cancelled real worker threads."""

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
import sys

import pytest

from aikey.model_work_fence import (FILENAME, SCHEMA, ModelWorkFence,
                                    ModelWorkFenceError, initialize)
from aikey.aiport_inference import FairInference
from aikey.worker import JobProcessor, WorkerError
from test_worker import command, configuration

pytest_plugins = ["test_worker"]
TX = "a" * 32


def setup(root):
    initialize(root, "0" * 32)
    return ModelWorkFence(root, enabled=True)


async def test_independent_reader_descriptions_keep_second_reader_protected(tmp_path):
    fence = setup(tmp_path)
    first = second = writer = None
    try:
        first = await fence.acquire()
        second = await fence.acquire()
        assert fence.status()["leases"] == 2
        assert fence.try_exclusive() is None
        first.close()
        assert fence.try_exclusive() is None  # Not dup()/one shared open description.
        second.close()
        writer = fence.try_exclusive()
        assert writer is not None
        assert fence.status()["maintenance"] == "writer_active"
        writer.begin(TX)
        writer.finish(TX)
    finally:
        for lease in (first, second, writer):
            if lease is not None:
                lease.close()
        fence.close()


async def test_hardlinked_consumers_share_binding_and_exclude_writer(tmp_path):
    owner = setup(tmp_path)
    peer = tmp_path / "peer"
    peer.mkdir(mode=0o700)
    os.link(tmp_path / FILENAME, peer / FILENAME)
    consumer = ModelWorkFence(peer, enabled=True)
    try:
        assert consumer.status()["binding"] == owner.status()["binding"]
        lease = await consumer.acquire()
        try:
            assert owner.try_exclusive() is None
        finally:
            lease.close()
        writer = owner.try_exclusive()
        try:
            writer.begin(TX)
            waiter = asyncio.create_task(consumer.acquire())
            await asyncio.sleep(0.02)
            assert not waiter.done()
            writer.finish(TX)
        finally:
            writer.close()
        lease = await asyncio.wait_for(waiter, 1)
        lease.close()
    finally:
        consumer.close()
        owner.close()


async def test_writer_loss_does_not_clear_durable_intent(tmp_path):
    fence = setup(tmp_path)
    writer = fence.try_exclusive()
    writer.begin(TX)
    writer.close()  # Same kernel effect as writer-process loss; no finish.
    assert fence.status()["maintenance"] == "unresolved"
    with pytest.raises(ModelWorkFenceError, match="unresolved"):
        fence.try_exclusive()
    with pytest.raises(TimeoutError):
        await fence.acquire(deadline=time.monotonic() + 0.06)
    assert fence.status()["leases"] == fence.status()["waiters"] == 0
    assert json.loads((tmp_path / FILENAME).read_text())["phase"] == "maintenance"
    fence.close()


def _lost_writer(root, ready):
    fence = ModelWorkFence(Path(root), enabled=True)
    lease = fence.try_exclusive()
    lease.begin(TX)
    ready.send("intent_durable")
    os._exit(0)  # Deliberate fixture crash: no Python cleanup/finally.


def test_actual_process_exit_leaves_intent_not_unlocked_authority(tmp_path):
    fence = setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=_lost_writer, args=(str(tmp_path), send))
    child.start()
    try:
        assert receive.poll(10) and receive.recv() == "intent_durable"
        child.join(5)
        assert child.exitcode == 0
        with pytest.raises(ModelWorkFenceError, match="unresolved"):
            fence.try_exclusive()
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)
        receive.close()
        send.close()
        fence.close()


async def test_writer_intent_blocks_new_work_and_cancellation_closes_waiter(tmp_path):
    fence = setup(tmp_path)
    writer = fence.try_exclusive()
    try:
        writer.begin(TX)
        waiter = asyncio.create_task(fence.acquire())
        await asyncio.sleep(0.02)
        assert not waiter.done() and fence.status()["waiters"] == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert fence.status()["waiters"] == 0
        with pytest.raises(ModelWorkFenceError, match="transaction_changed"):
            writer.finish("b" * 32)
        assert json.loads((tmp_path / FILENAME).read_text())["transaction"] == TX
        writer.finish(TX)
    finally:
        writer.close()
        fence.close()


@pytest.mark.parametrize("kind", ["missing", "symlink", "fifo", "public", "malformed",
                                  "duplicate", "empty", "oversize", "extra", "wrong_schema"])
def test_unsafe_markers_do_not_create_or_repair_anything(tmp_path, kind):
    if kind == "missing":
        with pytest.raises(ModelWorkFenceError):
            ModelWorkFence(tmp_path, enabled=True)
        assert list(tmp_path.iterdir()) == []
        return
    fence = setup(tmp_path)
    fence.close()
    path = tmp_path / FILENAME
    original = path.read_bytes()
    canary = tmp_path / "outside"
    canary.write_bytes(b"private canary")
    if kind == "symlink":
        path.unlink()
        path.symlink_to(canary)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "public":
        path.chmod(0o644)
    elif kind == "malformed":
        path.write_bytes(b"{")
    elif kind == "duplicate":
        path.write_bytes(original.replace(b'"phase":', b'"phase":"idle","phase":'))
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversize":
        path.write_bytes(b"x" * 2049)
    elif kind == "extra":
        path.write_bytes(original.replace(b'"phase":', b'"extra":1,"phase":'))
    else:
        path.write_bytes(original.replace(SCHEMA.encode(), b"wrong-schema"))
    with pytest.raises(ModelWorkFenceError):
        ModelWorkFence(tmp_path, enabled=True)
    assert canary.read_bytes() == b"private canary"


async def test_replacing_a_link_cannot_retarget_a_live_consumer(tmp_path):
    fence = setup(tmp_path)
    (tmp_path / FILENAME).rename(tmp_path / "retained")
    initialize(tmp_path, "1" * 32)
    with pytest.raises(ModelWorkFenceError, match="binding_changed"):
        await fence.acquire()
    with pytest.raises(ModelWorkFenceError, match="binding_changed"):
        fence.try_exclusive()
    fence.close()


async def test_disabled_default_creates_nothing_and_validates_boolean(tmp_path):
    fence = ModelWorkFence.from_state(tmp_path)
    lease = await fence.acquire()
    lease.close()
    assert not list(tmp_path.iterdir())
    assert fence.status()["enabled"] is False and fence.status()["binding"] is None
    fence.close()
    for invalid in (1, "yes", None, []):
        with pytest.raises(ModelWorkFenceError):
            ModelWorkFence(tmp_path, enabled=invalid)


def test_marker_is_explicit_enrollment_and_broken_marker_never_disables(tmp_path):
    initialize(tmp_path, "0"*32)
    fence = ModelWorkFence.from_state(tmp_path)
    try:
        assert fence.enabled is True
    finally:
        fence.close()
    (tmp_path/FILENAME).unlink()
    (tmp_path/FILENAME).symlink_to(tmp_path/"absent")
    with pytest.raises(ModelWorkFenceError):
        ModelWorkFence.from_state(tmp_path)


async def test_cancelled_async_model_waiter_keeps_actual_thread_fenced(tmp_path):
    fence = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocking():
        entered.set()
        assert release.wait(5)
        return ()

    async def result(*args):
        pass

    worker = FairInference(["020000000001"], load_detector=lambda: None,
                           on_result=result, max_frames_per_camera=None, work_fence=fence)
    waiter = asyncio.create_task(worker._call_in_thread(blocking))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert fence.try_exclusive() is None
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert fence.try_exclusive() is None
        assert fence.status()["leases"] == 1
        release.set()
        await worker.close()
        assert fence.status()["leases"] == 0
        writer = fence.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        release.set()
        await worker.close()
        fence.close()


async def test_real_worker_holds_fence_through_model_and_callback(services, tmp_path):
    owner = setup(tmp_path)
    config = configuration(services)
    services.release.clear()
    worker = JobProcessor(config, tmp_path)
    job = asyncio.create_task(worker.handle(command()))
    try:
        await asyncio.wait_for(services.entered.wait(), 2)
        assert owner.try_exclusive() is None
        services.release.set()
        assert (await job)["callback"] == "http_accepted"
        assert len(services.callbacks) == 1
        writer = owner.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        services.release.set()
        await worker.stop()
        owner.close()


async def test_blocked_worker_preserves_original_budget_without_media_or_model(services, tmp_path):
    owner = setup(tmp_path)
    config = configuration(services)
    writer = owner.try_exclusive()
    writer.begin(TX)
    worker = JobProcessor(config, tmp_path)
    request = command()
    request["timeoutMs"] = 100
    try:
        with pytest.raises(WorkerError, match="timed out"):
            await worker.handle(request)
        assert services.media_requests == services.requests == services.callbacks == []
        assert worker.status()["model_work_fence"]["leases"] == 0
        assert worker.status()["model_work_fence"]["waiters"] == 0
        writer.finish(TX)
    finally:
        writer.close()
        await worker.stop()
        owner.close()


def test_in_place_writer_truncation_does_not_make_waiting_consumer_fail(tmp_path, monkeypatch):
    fence = setup(tmp_path)
    writer = fence.try_exclusive()
    truncate = os.ftruncate
    observations = []

    def interpose(fd, length):
        truncate(fd, length)
        if fd != writer.fd or length != 0:
            return
        # Exact post-truncate/pre-write boundary, while the writer owns EX.
        peer = ModelWorkFence(tmp_path, enabled=True)
        try:
            observations.append(peer.status()["maintenance"])
            with pytest.raises(TimeoutError):
                asyncio.run(peer.acquire(deadline=time.monotonic()+0.02))
        finally:
            peer.close()

    monkeypatch.setattr(os, "ftruncate", interpose)
    try:
        writer.begin(TX)
        writer.finish(TX)
        assert observations == ["writer_active", "writer_active"]
    finally:
        writer.close()
        fence.close()


async def test_late_successful_marker_read_cannot_admit_expired_work(tmp_path, monkeypatch):
    from aikey import model_work_fence as module
    from types import SimpleNamespace
    fence = setup(tmp_path)
    clock = [1.0]
    original = module._read

    def late(fd):
        value = original(fd)
        clock[0] = 5.0
        return value

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "_read", late)
    try:
        with pytest.raises(TimeoutError):
            await fence.acquire(deadline=2.0)
        assert fence.status()["leases"] == 0
    finally:
        fence.close()


async def test_live_candidate_enrollment_uses_same_fence_without_starting_models(tmp_path, monkeypatch):
    from aikey.aiport_candidate import CandidateService, load_config
    from test_aiport_candidate import fixture_state, private_file
    config = fixture_state(tmp_path)
    config["paired_streams"] = [{"camera_mac": "020000000001", "source_ip": "192.168.10.1",
                                 "ffmpeg_path": sys.executable}]
    config["live_pool_detector"] = {"checkpoint_path": str(tmp_path/"not-loaded.pth"),
        "checkpoint_sha256": "a"*64, "threshold": 0.3, "smart_types": ["person"],
        "max_events_per_hour": 120}
    private_file(tmp_path/"config.json", json.dumps(config).encode())
    owner = setup(tmp_path)
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("no transport"))
    service = CandidateService(load_config(tmp_path/"config.json"), tmp_path)
    try:
        assert service._model_work_fence.status()["binding"] == owner.status()["binding"]
        assert service._inference._work_fence is service._model_work_fence
        assert service._inference._model is None
        writer = owner.try_exclusive()
        try:
            health = json.loads((await service._health(None)).text)
            assert health["model_work_fence"]["maintenance"] == "writer_active"
        finally:
            writer.close()
    finally:
        await service.stop()
        owner.close()


def test_marker_on_an_uncovered_port_mode_is_refused(tmp_path):
    from aikey.aiport_candidate import CandidateError, CandidateService
    from test_aiport_candidate import fixture_state
    config = fixture_state(tmp_path)
    owner = setup(tmp_path)
    try:
        with pytest.raises(CandidateError, match="live multi-camera"):
            CandidateService(config, tmp_path)
    finally:
        owner.close()

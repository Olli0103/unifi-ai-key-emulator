"""Private diagnostics do not participate in replay, callbacks or inference authority."""

import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import stat
import time

import pytest
import aiohttp

from aikey import worker_failure_notes as notes
from aikey.worker import (JobProcessor, WorkerError, _ControllerHTTPError, _failure_category)
from aikey.providers import ProviderError
from aikey.speech import SpeechError
from aikey.faces import FaceStoreError
from aikey.clip import ClipError
from test_worker import command, configuration

pytest_plugins = ["test_worker"]

JOB, FP = "a" * 64, "b" * 64


def record(store, job=JOB, fingerprint=FP, **changes):
    fields = dict(observed_at=time.time(), journal_state="failed", category="timeout")
    fields.update(changes)
    return store.record(job, fingerprint, **fields)


def test_lazy_lookup_and_first_note_survives_restart_without_relabeling(tmp_path):
    store = notes.FailureNotes(tmp_path)
    assert store.lookup(JOB, FP) == {"status": "not_recorded", "record": None}
    assert list(tmp_path.iterdir()) == []
    assert record(store) == "written"
    original = store.lookup(JOB, FP)
    assert original["record"]["category"] == "timeout"
    assert record(store, category="worker_error") == "already_recorded"
    assert notes.FailureNotes(tmp_path).lookup(JOB, FP) == original
    assert record(store, fingerprint="c" * 64, category="os_error") == "written"
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in store.root.iterdir())
    assert len(list(store.root.iterdir())) == 3  # Two notes, one lock, no pending file.


@pytest.mark.parametrize("changes", [
    {"category": "raw private exception"}, {"category": []},
    {"journal_state": "running"}, {"journal_state": False},
    {"observed_at": True}, {"observed_at": float("nan")},
    {"observed_at": float("inf")}, {"observed_at": 0},
    {"observed_at": time.time() + 86400}, {"observed_at": "now"}, {"observed_at": 10**1000},
    {"http_status": 500}, {"category": "controller_media_http", "http_status": True},
    {"category": "controller_callback_http", "http_status": 600},
    {"category": "controller_media_http", "http_status": None},
])
def test_invalid_fields_rejected_before_creating_paths(tmp_path, changes):
    assert record(notes.FailureNotes(tmp_path), **changes) == "unavailable"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("identifier", ["../outside", "a" * 63, "A" * 64, [], None])
def test_invalid_identity_before_io(tmp_path, identifier):
    store = notes.FailureNotes(tmp_path)
    assert record(store, job=identifier) == "unavailable"
    assert store.lookup(JOB, identifier)["status"] == "unavailable"
    assert not store.root.exists()


@pytest.mark.parametrize("kind", ["root_link", "root_mode", "leaf_link", "fifo", "leaf_mode",
                                  "malformed", "extra", "duplicate", "wrong_binding", "oversize"])
def test_unsafe_or_invalid_existing_note_is_not_followed_or_repaired(tmp_path, kind):
    store = notes.FailureNotes(tmp_path)
    assert record(store) == "written"
    target = next(store.root.glob("*.json"))
    before = target.read_bytes()
    canary = tmp_path / "outside"
    canary.write_bytes(b"PRIVATE CANARY")
    if kind == "root_link":
        store.root.rename(tmp_path / "retained")
        store.root.symlink_to(tmp_path / "retained", target_is_directory=True)
    elif kind == "root_mode":
        store.root.chmod(0o755)
    elif kind == "leaf_link":
        target.unlink()
        target.symlink_to(canary)
    elif kind == "fifo":
        target.unlink()
        os.mkfifo(target, 0o600)
    elif kind == "leaf_mode":
        target.chmod(0o644)
    elif kind == "malformed":
        target.write_bytes(b"{broken")
    elif kind == "extra":
        target.write_bytes(before.replace(b'"schema":', b'"extra": "CANARY", "schema":'))
    elif kind == "duplicate":
        target.write_bytes(before.replace(b'"schema":', b'"schema": "duplicate", "schema":'))
    elif kind == "wrong_binding":
        target.write_bytes(before.replace(JOB.encode(), b"c" * 64))
    else:
        target.write_bytes(b"x" * 2049)
    assert store.lookup(JOB, FP)["status"] == "unavailable"
    assert record(store) == "unavailable"
    assert canary.read_bytes() == b"PRIVATE CANARY"


def test_nonwaiting_lock_capacity_and_interrupted_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "CAPACITY", 1)
    store = notes.FailureNotes(tmp_path)
    assert record(store) == "written"
    with (store.root / "writer.lock").open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert record(store) == "busy"
    assert record(store) == "already_recorded"
    assert record(store, job="c" * 64) == "capacity"
    monkeypatch.setattr(notes, "CAPACITY", 1024)
    interrupted = store.root / ".pending-interrupted"
    interrupted.write_bytes(b"retain")
    assert record(store, job="c" * 64) == "unavailable"
    assert interrupted.read_bytes() == b"retain"


def _competing_writer(root, job, ready, go, result):
    notes.CAPACITY = 1
    ready.put(job)
    if not go.wait(5):
        raise RuntimeError("fixture barrier expired")
    result.put(record(notes.FailureNotes(Path(root)), job=job))


def test_two_real_processes_cannot_exceed_capacity(tmp_path):
    # Spawned processes, real kernel locks; no claim of measured request overlap.
    context = multiprocessing.get_context("spawn")
    ready, result, go = context.Queue(), context.Queue(), context.Event()
    children = [context.Process(target=_competing_writer,
                args=(str(tmp_path), value * 64, ready, go, result)) for value in "ac"]
    try:
        for child in children:
            child.start()
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {"a" * 64, "c" * 64}
        go.set()
        outcomes = [result.get(timeout=10), result.get(timeout=10)]
        assert outcomes.count("written") == 1
        # macOS can return ENOENT when O_CREAT|O_NOFOLLOW races on the
        # initially absent lock leaf. This best-effort diagnostic fails closed;
        # it must not retry a job or pretend every exception was retained.
        assert set(outcomes) <= {"written", "busy", "capacity", "unavailable"}
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert len(list((tmp_path / "worker-failure-notes").glob("*.json"))) == 1
    finally:
        go.set()
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(5)
        for queue in (ready, result):
            queue.close()
            queue.join_thread()


def test_directory_swap_after_open_never_retargets_publication(tmp_path, monkeypatch):
    store = notes.FailureNotes(tmp_path)
    assert record(store) == "written"
    moved, outside = tmp_path / "retained", tmp_path / "outside"
    outside.mkdir(mode=0o700)
    original = store._directory

    def swap(*, create):
        root_fd = original(create=create)
        store.root.rename(moved)
        store.root.symlink_to(outside, target_is_directory=True)
        return root_fd

    monkeypatch.setattr(store, "_directory", swap)
    assert record(store, job="c" * 64) == "written"
    assert list(outside.iterdir()) == []
    assert len(list(moved.glob("*.json"))) == 2
    assert not list(moved.glob(".pending-*"))


@pytest.mark.parametrize(("error", "category", "status"), [
    (TimeoutError("private"), "timeout", None),
    (_ControllerHTTPError("media", 404), "controller_media_http", 404),
    (_ControllerHTTPError("callback", 503), "controller_callback_http", 503),
    (ProviderError("private"), "vision_provider_error", None),
    (SpeechError("private"), "speech_provider_error", None),
    (FaceStoreError("private"), "face_store_error", None),
    (ClipError("private"), "local_clip_error", None),
    (OSError("private"), "os_error", None),
    (aiohttp.ClientError("private"), "http_transport_error", None),
    (WorkerError("private timeout HTTP 404"), "worker_error", None),
    (ValueError("private"), "unclassified_exception", None),
])
def test_categories_use_types_not_exception_text(error, category, status):
    assert _failure_category(error) == (category, status)


def test_explicit_causes_are_bounded_and_cycles_do_not_hang():
    outer = WorkerError("fixture")
    outer.__cause__ = TimeoutError("private")
    assert _failure_category(outer) == ("timeout", None)
    outer.__cause__ = outer
    assert _failure_category(outer) == ("worker_error", None)


@pytest.mark.parametrize("point", ["link", "fsync"])
def test_io_failure_preserves_guard_and_reports_uncertainty(tmp_path, monkeypatch, point):
    store = notes.FailureNotes(tmp_path)
    assert record(store) == "written"
    guard = tmp_path / "worker-archive"
    guard.mkdir()
    (guard / "synthetic-tombstone").write_bytes(b"unchanged replay guard")
    before = next(store.root.glob("*.json")).read_bytes()

    def refuse(*args, **kwargs):
        raise OSError("private fixture error")

    monkeypatch.setattr(notes.os, point, refuse)
    assert record(store, job="c" * 64) == "unavailable"
    assert (guard / "synthetic-tombstone").read_bytes() == b"unchanged replay guard"
    assert store.lookup(JOB, FP)["record"] is not None
    assert next(store.root.glob("*.json")).read_bytes() == before
    assert not list(store.root.glob(".pending-*"))


@pytest.mark.parametrize("boundary", ["media", "model", "callback", "timeout"])
async def test_real_loopback_failure_preserves_note_and_original_journal(services, tmp_path, boundary):
    if boundary == "media":
        services.media_status = 404
        category, status, expected_state = "controller_media_http", 404, "failed"
    elif boundary == "model":
        services.model_status = 500
        category, status, expected_state = "vision_provider_error", None, "failed"
    elif boundary == "callback":
        services.callback_status = 503
        category, status, expected_state = "controller_callback_http", 503, "callback_uncertain"
    else:
        services.release.clear()
        category, status, expected_state = "timeout", None, "failed"
    config = configuration(services)
    request = command()
    if boundary == "timeout":
        request["timeoutMs"] = 100
    processor = JobProcessor(config, tmp_path)
    try:
        with pytest.raises(WorkerError):
            await processor.handle(request)
        [(job, history)] = list(processor._history.items())
        note = processor._failure_notes.lookup(job, history["fingerprint"])["record"]
        assert (note["category"], note["http_status"], history["state"]) == (
            category, status, expected_state)
        assert note["observed_at"] == history["updatedAt"]
        encoded = json.dumps(note)
        assert all(value not in encoded for value in (
            "camera-fixture", "event-fixture", services.origin, "Synthetic local test response"))
        assert processor.status()["failure_notes"]["current_process"]["written"] == 1
        counts = (len(services.media_requests), len(services.requests), len(services.callbacks))
        if boundary == "media":
            assert counts == (1, 0, 0)
        elif boundary != "callback":
            assert counts == (1, 1, 0)
        if expected_state == "failed":
            processor._private_archive_dir(processor.archive_dir)
            processor._archive_terminal(job)
            tombstone = processor._archived_record(job)
            assert set(tombstone) == {"schema", "jobId", "fingerprint", "state", "updatedAt"}
        await processor.stop()
        restarted = JobProcessor(config, tmp_path)
        try:
            assert restarted._failure_notes.lookup(job, history["fingerprint"])["record"] == note
            with pytest.raises(WorkerError, match="cannot be replayed|uncertain"):
                await restarted.handle(request)
            assert (len(services.media_requests), len(services.requests), len(services.callbacks)) == counts
        finally:
            await restarted.stop()
    finally:
        services.release.set()
        await processor.stop()


@pytest.mark.parametrize("result", [OSError("private canary"), RuntimeError("private canary"),
                                   [], False, None, "unknown"])
async def test_diagnostic_fault_cannot_replace_original_error(services, tmp_path, monkeypatch, result):
    services.media_status = 404
    processor = JobProcessor(configuration(services), tmp_path)

    def broken(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(processor._failure_notes, "record", broken)
    try:
        with pytest.raises(WorkerError, match="Controller media request returned HTTP 404"):
            await processor.handle(command())
        assert next(iter(processor._history.values()))["state"] == "failed"
        assert processor.status()["failure_notes"]["current_process"]["unavailable"] == 1
        assert services.requests == services.callbacks == []
        assert not processor._failure_notes.root.exists()
    finally:
        await processor.stop()


async def test_success_and_health_do_not_create_failure_directory(services, tmp_path):
    processor = JobProcessor(configuration(services), tmp_path)
    try:
        assert processor.status()["failure_notes"]["current_process"] == dict.fromkeys(notes.OUTCOMES, 0)
        await processor.handle(command())
        assert not processor._failure_notes.root.exists()
    finally:
        await processor.stop()

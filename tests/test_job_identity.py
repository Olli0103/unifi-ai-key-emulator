"""Per-operation job identity for one event (#1). Normalization and admission only; no media."""

import hashlib
import shutil
import time
from copy import deepcopy

import pytest

from aikey import clip
from aikey.worker import JobProcessor, WorkerError
import test_find_anything as fa
import test_local_faces as lf

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg path required by config")
CAMERA, EVENT = fa.CAMERA, fa.EVENT


def worker(tmp_path):
    origin = "http://127.0.0.1:9"
    options = {"runtime": {"mode": "lab"}, "controller_origins": [origin],
               "device": {"mac": "02:00:00:00:00:98"},
               "inference": {"base_url": origin + "/v1", "model": "synthetic-vision"},
               "search": {"enabled": True, "profile": clip.PROFILE},
               "find_anything": {"clip_server": origin, "index_camera_ids": [CAMERA]},
               "face_recognition": {"server": origin, "camera_ids": [CAMERA]},
               "worker": {"max_queue": 2, "timeout_s": 20, "ffmpeg_path": shutil.which("ffmpeg"),
                          "test_scope": {"kind": "recognizeKeyFrames", "permit_id": "once",
                                         "camera_id": CAMERA}}}
    return JobProcessor(options, tmp_path)


def face_task():
    task = lf.task(camera=CAMERA)
    payload = task["payload"]
    payload["event"] = EVENT
    payload["reqUrl"] = fa.task()["payload"]["reqUrl"]
    payload.update(start=fa.START, end=fa.END, keyMoments=[fa.START + 1000])
    for item in payload["faceMeta"]:
        item["ts"] = fa.START + 1500
    return task


def index_task():
    return fa.task()                                           # postVLM false


def caption_task():
    task = fa.task()
    task["payload"]["postVLM"] = True
    return task


def legacy_id():
    return hashlib.sha256(f"recognizeKeyFrames:{CAMERA}:{EVENT}".encode()).hexdigest()


def test_each_operation_on_one_event_has_its_own_identity(tmp_path):
    w = worker(tmp_path)
    face, index, caption = (w._normalize(t) for t in (face_task(), index_task(), caption_task()))
    assert [face[2], index[2], caption[2]] == ["recognizeFaces", "indexKeyFrames", "recognizeKeyFrames"]
    assert len({face[0], index[0], caption[0]}) == 3
    assert caption[0] == legacy_id()                           # captions keep permit/budget identity


def test_an_identical_resend_keeps_the_same_identity(tmp_path):
    w = worker(tmp_path)
    assert w._normalize(face_task())[:2] == w._normalize(face_task())[:2]
    assert w._normalize(index_task())[:2] == w._normalize(index_task())[:2]


def test_a_job_recorded_under_the_former_shared_identity_still_dedupes(tmp_path):
    w = worker(tmp_path)
    _, fingerprint, *_ = w._normalize(face_task())
    w._history[legacy_id()] = {"jobId": legacy_id(), "fingerprint": fingerprint, "state": "completed",
                               "updatedAt": time.time(), "operation": "recognizeFaces"}
    assert w._normalize(face_task())[0] == legacy_id()         # identical resend: the same old job
    assert w._normalize(index_task())[0] != legacy_id()        # a different task no longer collides


async def test_a_second_different_task_for_the_event_is_not_an_identity_conflict(tmp_path, monkeypatch):
    w = worker(tmp_path)
    face_id, face_fp, *_ = w._normalize(face_task())
    w._history[legacy_id()] = {"jobId": legacy_id(), "fingerprint": face_fp, "state": "completed",
                               "updatedAt": time.time(), "operation": "recognizeFaces"}
    queued = []
    monkeypatch.setattr(w._queue, "put_nowait", lambda item: queued.append(item[2].operation))
    try:
        result = await w.submit(index_task())
    finally:
        await w.stop()
    assert result["accepted"] is True and result["duplicate"] is False and queued == ["indexKeyFrames"]


async def test_the_same_operation_with_changed_input_is_still_refused(tmp_path):
    w = worker(tmp_path)
    job_id, fingerprint, *_ = w._normalize(face_task())
    w._history[job_id] = {"jobId": job_id, "fingerprint": fingerprint, "state": "completed",
                          "updatedAt": time.time(), "operation": "recognizeFaces"}
    changed = deepcopy(face_task())
    changed["payload"]["keyMoments"] = [fa.START + 2000]
    try:
        with pytest.raises(WorkerError, match="identity reused with different input"):
            await w.submit(changed)
    finally:
        await w.stop()

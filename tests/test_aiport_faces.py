"""Local face embeddings for paired AI Port cameras (#20, #28). Synthetic images and fake models."""

import asyncio
from dataclasses import replace
import hashlib
import json
import math
import sys
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from aikey.aiport_faces import (EMBEDDING_SIZE, FaceEngine, FaceError, FaceResult, decode_yunet,
                                estimate_pose, make_face_snapshot, mean_embedding, send_gate,
                                similarity_transform, verify_model, _TEMPLATE)
from aikey.aiport_candidate import CandidateError, load_config
from aikey.aiport_tracking import TrackChange
from test_aiport_candidate import fixture_state, private_file

np = pytest.importorskip("numpy")
CAMERA, OTHER = "2A1122334455", "2A1122334466"


def jpeg(size=(640, 360)):
    image = Image.new("RGB", size, (90, 90, 90))
    draw = ImageDraw.Draw(image)
    draw.ellipse((280, 40, 360, 140), fill=(200, 170, 150))       # a head-like blob
    draw.rectangle((270, 140, 370, 330), fill=(40, 60, 120))
    out = BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


def test_the_similarity_transform_recovers_scale_rotation_and_shift():
    angle, scale, shift = math.radians(12), 1.7, (5.0, -3.0)
    src = [(x, y) for x, y in _TEMPLATE]
    dst = [(scale * (x * math.cos(angle) - y * math.sin(angle)) + shift[0],
            scale * (x * math.sin(angle) + y * math.cos(angle)) + shift[1]) for x, y in src]
    a, b, tx, c, d, ty = similarity_transform(src, dst)
    for (x, y), (u, v) in zip(src, dst):
        assert abs(a * x + b * y + tx - u) < 1e-6 and abs(c * x + d * y + ty - v) < 1e-6
    with pytest.raises(FaceError):
        similarity_transform([(1, 1)] * 5, dst)


def test_pose_is_frontal_for_the_template_and_turns_with_the_nose():
    assert all(abs(value) < 15 for value in estimate_pose(_TEMPLATE).values())
    turned = list(_TEMPLATE)
    turned[2] = (turned[2][0] + 12, turned[2][1])
    assert estimate_pose(turned)["yaw"] > 20


def yunet_outputs(cell=None, score=0.95):
    out = {}
    for stride in (8, 16, 32):
        n = (640 // stride) ** 2
        out[f"cls_{stride}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{stride}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{stride}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{stride}"] = np.zeros((1, n, 10), np.float32)
    if cell is not None:
        stride, row, col = cell
        index = row * (640 // stride) + col
        out[f"cls_{stride}"][0, index, 0] = score
        out[f"obj_{stride}"][0, index, 0] = score
        out[f"bbox_{stride}"][0, index] = [0.5, 0.5, math.log(8), math.log(10)]
        out[f"kps_{stride}"][0, index] = [-1.2, -1.6, 1.2, -1.6, 0, 0, -1.0, 1.8, 1.0, 1.8]
    return out


def test_yunet_decoding_maps_cells_to_pixels_and_drops_weak_faces():
    faces = decode_yunet(yunet_outputs((16, 10, 20)), threshold=0.6)
    assert len(faces) == 1
    score, (x, y, w, h), points = faces[0]
    assert score == pytest.approx(0.95, abs=1e-3)
    assert (x + w / 2, y + h / 2) == pytest.approx((328, 168)) and (w, h) == pytest.approx((128, 160))
    assert points[0] == pytest.approx(((20 - 1.2) * 16, (10 - 1.6) * 16))
    assert decode_yunet(yunet_outputs((16, 10, 20), score=0.3), threshold=0.6) == []


class Session:
    def __init__(self, kind, outputs=None):
        self.kind, self.outputs, self.calls = kind, outputs, []

    class _IO:
        def __init__(self, name):
            self.name = name

    def get_inputs(self):
        return [self._IO("input" if self.kind == "detector" else "data")]

    def get_outputs(self):
        return [self._IO(name) for name in self.outputs] if self.kind == "detector" else [self._IO("fc1")]

    def run(self, names, feeds):
        tensor = next(iter(feeds.values()))
        self.calls.append(tensor.shape)
        if self.kind == "detector":
            return [self.outputs[name] for name in names]
        vector = np.arange(1, EMBEDDING_SIZE + 1, dtype=np.float32)[None]
        return [vector]


def engine(outputs):
    sessions = {"det": Session("detector", outputs), "emb": Session("embedder")}
    return FaceEngine("det", "emb", session_factory=lambda path: sessions[path]), sessions


def test_the_engine_returns_an_aligned_normalised_512_value_face():
    face_engine, sessions = engine(yunet_outputs((16, 10, 20)))
    result = face_engine.analyse(jpeg(), (0.4, 0.1, 0.6, 0.95))
    assert sessions["det"].calls == [(1, 3, 640, 640)] and sessions["emb"].calls == [(1, 3, 112, 112)]
    assert len(result.embedding) == EMBEDDING_SIZE
    assert math.isclose(math.sqrt(sum(v * v for v in result.embedding)), 1.0, rel_tol=1e-5)
    x1, y1, x2, y2 = result.box
    assert 0.4 <= x1 < x2 <= 0.6 and 0.05 <= y1 < y2 <= 0.62          # inside the person's head area
    assert len(result.landmarks) == 5 and 0 <= result.quality <= 1 and 0 <= result.blurness <= 1


def test_no_face_a_tiny_person_or_a_bad_frame_yield_no_embedding():
    face_engine, sessions = engine(yunet_outputs(None))
    assert face_engine.analyse(jpeg(), (0.4, 0.1, 0.6, 0.95)) is None
    assert face_engine.analyse(jpeg(), (0.5, 0.5, 0.52, 0.53)) is None          # too small to try
    assert sessions["emb"].calls == []
    with pytest.raises(FaceError):
        face_engine.analyse(b"not a jpeg", (0.4, 0.1, 0.6, 0.95))
    with pytest.raises(FaceError):
        face_engine.analyse(jpeg(), (0.6, 0.1, 0.4, 0.95))


def result():
    return FaceResult((0.45, 0.12, 0.55, 0.3), tuple((0.47 + i * 0.01, 0.2) for i in range(5)),
                      0.9, tuple([1 / math.sqrt(EMBEDDING_SIZE)] * EMBEDDING_SIZE), 0.8, 0.1,
                      {"yaw": 1.0, "pitch": 0.0, "roll": 0.0})


def test_face_records_match_protects_own_face_attribute_names():
    attrs = result().attributes(1_000_000_001, (1,))
    assert {"objectType", "trackerId", "zone", "faceEmbed", "faceLandmarks", "qualityScore",
            "blurness", "facePose", "faceMask", "faceVerifyStatus", "namesTopK",
            "topKCandidate", "matchedName"} == set(attrs)
    assert [v["verifyType"] for v in attrs["faceVerifyStatus"]] == [
        "is_invalid_cropped", "occluded", "blur_motion", "blur_focus", "bad_pose", "non_face"]
    assert attrs["objectType"] == "face" and len(attrs["faceEmbed"]) == 512
    assert attrs["faceMask"]["val"] in {"face", "face_mask"}                # Protect's vocabulary
    assert type(attrs["qualityScore"]) is int and attrs["qualityScore"] == 80
    assert type(attrs["faceMask"]["confidence"]) is int
    assert type(result().descriptor(1, (1,))["attributes"]["faceMask"]["confidence"]) is int
    assert result().descriptor(900_001, (1,))["attributes"]["faceMask"]["val"] == "face"
    assert len(attrs["faceLandmarks"]) == 10 and all(0 <= v <= 1000 for v in attrs["faceLandmarks"])
    descriptor = result().descriptor(1_000_000_001, (1,))
    assert descriptor["objectType"] == "face" and descriptor["coord"] == [450, 120, 100, 180]
    snapshot = make_face_snapshot(jpeg(), result(), 1_000_000_001, 1_700_000_000_000, filename_id=7)
    assert snapshot.metadata["smartDetectSnapshotType"] == "face"
    assert snapshot.metadata["trackerID"] == 1_000_000_001 and snapshot.jpeg[:2] == b"\xff\xd8"


def test_models_are_pinned_by_digest(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"synthetic")
    digest = hashlib.sha256(b"synthetic").hexdigest()
    assert verify_model(str(model), digest) == str(model)
    for path, pin in ((str(model), "0" * 64), (str(tmp_path / "missing"), digest), ("rel.onnx", digest)):
        with pytest.raises(FaceError):
            verify_model(path, pin)


def pool_config(tmp_path, *, face=None):
    config = fixture_state(tmp_path)
    private_file(tmp_path / "api-key", b"synthetic-test-key\n")
    config["paired_streams"] = [{"camera_mac": mac, "source_ip": "192.168.10.1",
                                 "ffmpeg_path": sys.executable} for mac in (CAMERA, OTHER)]
    config["live_pool_detector"] = {
        "inference_backend": "vision_api", "threshold": 0.8, "smart_types": ["person"],
        "max_events_per_hour": 12, "max_requests_per_hour": 12,
        "provider_config": {"provider": "openai", "model": "gpt-6-luna",
                            "base_url": "https://api.openai.com/v1", "allow_remote": True,
                            "max_output_tokens": 256, "api_key_file": str(tmp_path / "api-key")}}
    config["live_face"] = face or {"cameras": [CAMERA], "detector_path": "/models/d.onnx",
                                   "detector_sha256": "a" * 64, "embedder_path": "/models/e.onnx",
                                   "embedder_sha256": "b" * 64}
    return config


def test_face_policy_requires_pinned_models_and_pool_cameras(tmp_path):
    path = tmp_path / "ok.json"
    private_file(path, json.dumps(pool_config(tmp_path)).encode())
    assert load_config(path, check_decoder_executable=False)["live_face"]["cameras"] == [CAMERA]
    good = pool_config(tmp_path)["live_face"]
    for bad in (dict(good, cameras=["2A99887766AA"]), dict(good, cameras=[]),
                dict(good, detector_sha256="x"), dict(good, embedder_path="e.onnx"),
                dict(good, extra=1)):
        path = tmp_path / "bad.json"
        private_file(path, json.dumps(pool_config(tmp_path, face=bad)).encode())
        with pytest.raises(CandidateError):
            load_config(path, check_decoder_executable=False)


class Sink:
    def __init__(self):
        self.messages = []

    async def send_bytes(self, raw):
        self.messages.append(json.loads(raw))

    def events(self, name):
        return [m["payload"] for m in self.messages if m["functionName"] == name
                and m.get("inResponseTo") == 0]


def change(edge, track=5):
    return TrackChange(edge, track, "person", "person", 0.9, (0.4, 0.1, 0.6, 0.95))


async def test_a_found_face_joins_the_event_as_its_own_linked_tracker(tmp_path):
    from aikey.aiport_camera_engine import CameraEventCandidate
    from aikey.aiport_candidate import CandidateService
    service = CandidateService(pool_config(tmp_path), tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": CAMERA}, {"deviceID": OTHER}]
    service._face_engine, _ = engine(yunet_outputs((32, 5, 10)))       # an 80 px face: sendable
    service._face_enabled[CAMERA] = True
    sink = Sink()
    service._current_ws = sink
    frame = jpeg()
    try:
        await service._publish_pool_candidates((CameraEventCandidate(CAMERA, change("enter"), (1,)),),
                                               frame=frame)
        await service._face_tasks[CAMERA]
        await service._publish_pool_candidates((CameraEventCandidate(CAMERA, change("leave"), (1,)),))
        enter, leave = sink.events("EventSmartDetect")
        assert "face" not in enter["objectTypes"]                     # found after the enter
        face_id = next(d["trackerID"] for d in leave["descriptors"] if d["objectType"] == "face")
        assert "face" in leave["objectTypes"]
        attrs = leave["trackerIDAttrMap"]
        assert attrs["5"]["associatedFaceTrackerID"] == face_id
        assert len(attrs[str(face_id)]["faceEmbed"]) == 512
        assert any(s["smartDetectSnapshotType"] == "face" for s in leave["smartDetectSnapshots"])
        assert service.faces_sent == 1 and 900_000 < face_id < 1_000_000
        health = service._face_camera_health(CAMERA)
        assert health["listed"] and health["enabled"] and health["engine"]
        assert (health["analyses"], health["detected"], health["kept"], health["sent"]) == (1, 1, 1, 1)
        assert sum(service.face_yaw_bands.values()) == sum(service.face_px_bands.values()) == 1
        assert service._face_camera_health(OTHER)["analyses"] == 0
    finally:
        await service.stop()


async def test_no_face_work_while_protect_has_face_off_or_for_other_cameras(tmp_path):
    from aikey.aiport_camera_engine import CameraEventCandidate
    from aikey.aiport_candidate import CandidateService
    service = CandidateService(pool_config(tmp_path), tmp_path)
    service._params_agreed = True
    service.ingress.list_streams = lambda: [{"deviceID": CAMERA}, {"deviceID": OTHER}]
    service._face_engine, sessions = engine(yunet_outputs((16, 10, 20)))
    sink = Sink()
    service._current_ws = sink
    try:
        for camera in (CAMERA, OTHER):
            await service._publish_pool_candidates(
                (CameraEventCandidate(camera, change("enter"), (1,)),), frame=jpeg())
        await asyncio.sleep(0)
        assert service._face_tasks == {} and sessions["det"].calls == []
        assert all("face" not in e["objectTypes"] for e in sink.events("EventSmartDetect"))
    finally:
        await service.stop()


async def test_face_in_protects_smart_settings_is_stripped_and_remembered(tmp_path):
    from aikey.aiport_candidate import CandidateService
    service = CandidateService(pool_config(tmp_path), tmp_path)
    service._params_agreed = True
    sink = Sink()
    service._current_ws = sink
    payload = {"deviceID": CAMERA, "enableSmartDetect": ["person", "face"],
               "eventStartMSec": 0, "eventStopMSec": 1000}
    try:
        await service._handle_pool_smart_settings(sink, 9, payload)
        assert service._face_enabled[CAMERA] is True
        assert "unsupported_smart_feature" not in json.dumps(service.smart_settings_rejection_reasons)
        await service._handle_pool_smart_settings(sink, 10, dict(payload, enableSmartDetect=["person"]))
        assert service._face_enabled[CAMERA] is False
    finally:
        await service.stop()


def test_the_default_sessions_keep_a_single_copy_of_the_weights(monkeypatch):
    import sys
    import types
    created = []

    class Options:
        def __init__(self):
            self.entries = {}

        def add_session_config_entry(self, key, value):
            self.entries[key] = value

    fake = types.SimpleNamespace(
        SessionOptions=Options,
        GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_BASIC="basic"),
        InferenceSession=lambda path, options, providers: created.append(options) or path)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    FaceEngine("detector.onnx", "embedder.onnx")
    assert len(created) == 2
    assert all(o.graph_optimization_level == "basic" and o.intra_op_num_threads == 2
               and o.entries == {"session.disable_prepacking": "1"} for o in created)


def test_only_faces_that_can_identify_someone_are_sent():
    good = result()
    assert send_gate(replace(good, face_px=80.0)) is None
    assert send_gate(replace(good, face_px=30.0)) == "small"
    assert send_gate(replace(good, face_px=80.0, pose={"yaw": -60.0, "pitch": 0.0, "roll": 0.0})) is None
    assert send_gate(replace(good, face_px=80.0, pose={"yaw": -70.0, "pitch": 0.0, "roll": 0.0})) == "turned"
    assert send_gate(replace(good, face_px=80.0, blurness=0.95)) == "blurred"


def test_a_track_sends_the_normalised_mean_of_its_best_embeddings():
    a = tuple([1.0] + [0.0] * (EMBEDDING_SIZE - 1))
    b = tuple([0.0, 1.0] + [0.0] * (EMBEDDING_SIZE - 2))
    mean = mean_embedding([a, b])
    assert mean[0] == pytest.approx(mean[1]) == pytest.approx(1 / math.sqrt(2))
    assert sum(v * v for v in mean) == pytest.approx(1.0)
    with pytest.raises(FaceError):
        mean_embedding([])


def test_face_snapshots_keep_up_to_512_pixels():
    frame = BytesIO()
    Image.new("RGB", (3840, 2160), (120, 110, 100)).save(frame, format="JPEG")
    face = replace(result(), box=(0.4, 0.2, 0.55, 0.45))                # a 576 px face in 4K
    snapshot = make_face_snapshot(frame.getvalue(), face, 1_000_000_001, 1_700_000_000_000,
                                  filename_id=7)
    assert max(snapshot.metadata["smartDetectSnapshotWidth"],
               snapshot.metadata["smartDetectSnapshotHeight"]) == 512


def test_face_angles_and_sizes_fall_into_fixed_bands():
    from aikey.aiport_faces import FACE_PX_BANDS, YAW_BANDS, band
    assert [band(v, YAW_BANDS) for v in (0, 30, 49.9, 64, 90)] == [
        "under_30", "30_to_50", "30_to_50", "50_to_65", "over_65"]
    assert [band(v, FACE_PX_BANDS) for v in (12, 40, 120, 600)] == [
        "under_40", "40_to_80", "80_to_160", "over_160"]


def test_a_person_without_a_sendable_face_gets_more_tries(tmp_path, monkeypatch):
    import aikey.aiport_candidate as candidate
    service = candidate.CandidateService(pool_config(tmp_path), tmp_path)
    service._face_engine, _ = engine(yunet_outputs())                     # finds no face
    clock, scheduled = [1000.0], []

    class Done:
        def done(self):
            return True

    def fake_task(coro, name=None):
        coro.close()
        scheduled.append(name)
        return Done()
    monkeypatch.setattr(candidate.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(candidate.asyncio, "create_task", fake_task)
    session = {}
    for _ in range(12):
        service._schedule_face(CAMERA, session, change("enter"), b"")
        clock[0] += 3
    assert len(scheduled) == candidate.FACE_TRIES_WITHOUT_FACE == 8

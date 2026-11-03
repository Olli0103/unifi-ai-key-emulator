"""Fictional vectors/models only; no encoder inference or private identity reads."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey.face_server import build_app, embedding_space
from aikey.faces import FaceStore, FaceStoreError, embed_single_face
from aikey.worker import JobProcessor, WorkerError

SPACE_A, SPACE_B = 'a' * 64, 'b' * 64
VECTOR = [1.0] + [0.0] * 127


def legacy_store(tmp_path):
    store = FaceStore(tmp_path)
    store.directory.mkdir(mode=0o700)
    store.path.write_text(json.dumps({'schema': 1, 'identities': {
        'Fictional legacy identity': {'created': 1, 'templates': [VECTOR]}}}))
    store.path.chmod(0o600)
    return store


@pytest.mark.parametrize('operation', ['enroll', 'match'])
def test_same_dimension_other_model_is_refused_without_changing_bytes(tmp_path, operation):
    store = FaceStore(tmp_path)
    store.enroll('Fictional identity', VECTOR, embedding_space=SPACE_A)
    before = store.path.read_bytes()
    with pytest.raises(FaceStoreError, match='^face_embedding_space_mismatch$'):
        if operation == 'enroll':
            store.enroll('Fictional wrong-space identity', VECTOR, embedding_space=SPACE_B)
        else:
            store.match(VECTOR, embedding_space=SPACE_B)
    assert store.path.read_bytes() == before
    assert store.match(VECTOR, embedding_space=SPACE_A)[0] == 'Fictional identity'


@pytest.mark.parametrize('space', [None, True, 1, [], {}, '', 'A' * 64, 'a' * 63, 'a' * 65,
                                    'https://private-canary.invalid/'])
@pytest.mark.parametrize('operation', ['enroll', 'match'])
def test_missing_or_malformed_space_refused_before_any_store_access(tmp_path, space, operation):
    store = FaceStore(tmp_path)
    with pytest.raises(FaceStoreError, match='^face_embedding_space_invalid$'):
        if operation == 'enroll':
            store.enroll('Fictional identity', VECTOR, embedding_space=space)
        else:
            store.match(VECTOR, embedding_space=space)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('operation', ['enroll', 'match'])
def test_legacy_vectors_are_not_automatically_relabelled(tmp_path, operation):
    store = legacy_store(tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(FaceStoreError, match='^face_store_migration_required$'):
        if operation == 'enroll':
            store.enroll('Fictional added identity', VECTOR, embedding_space=SPACE_A)
        else:
            store.match(VECTOR, embedding_space=SPACE_A)
    assert store.path.read_bytes() == before
    assert store.names() == ['Fictional legacy identity']
    assert store.snapshot()['identities'][0]['templates'] == 1


@pytest.mark.parametrize('operation', ['delete', 'purge'])
def test_explicit_legacy_removal_then_fresh_enrollment_survives_reload(tmp_path, operation):
    store = legacy_store(tmp_path)
    revision = store.snapshot()['revision']
    if operation == 'delete':
        assert store.delete('Fictional legacy identity', expected_revision=revision)
    else:
        store.purge(expected_revision=revision)
    store.enroll('Fictional new identity', VECTOR, embedding_space=SPACE_B)
    assert FaceStore(tmp_path).match(VECTOR, embedding_space=SPACE_B)[0] == 'Fictional new identity'
    raw = json.loads(store.path.read_bytes())
    assert raw['schema'] == 2 and raw['embedding_space'] == SPACE_B
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_deleting_last_bound_identity_does_not_silently_switch_model(tmp_path):
    store = FaceStore(tmp_path)
    store.enroll('Fictional identity', VECTOR, embedding_space=SPACE_A)
    store.delete('Fictional identity')
    with pytest.raises(FaceStoreError, match='^face_embedding_space_mismatch$'):
        store.enroll('Fictional new identity', VECTOR, embedding_space=SPACE_B)
    store.purge()
    store.enroll('Fictional new identity', VECTOR, embedding_space=SPACE_B)


def test_space_changes_with_graph_pipeline_inputs_not_model_paths():
    original = embedding_space(SPACE_A, SPACE_B, 'fictional-opencv-1')
    assert original == embedding_space(SPACE_A, SPACE_B, 'fictional-opencv-1')
    assert len({original, embedding_space(SPACE_B, SPACE_B, 'fictional-opencv-1'),
                embedding_space(SPACE_A, SPACE_A, 'fictional-opencv-1'),
                embedding_space(SPACE_A, SPACE_B, 'fictional-opencv-2')}) == 4


async def test_server_and_enrollment_transport_preserve_space_with_vector():
    calls = []
    def analyze(image, regions):
        calls.append((image, regions))
        return [((.1, .2, .3, .4), .9, VECTOR)]
    async with TestServer(build_app(analyze, embedding_space=SPACE_A)) as server:
        vector, space = await asyncio.to_thread(embed_single_face, b'\xff\xd8\xff' + b'FICTIONAL-JPEG' * 6,
                                                str(server.make_url('')).rstrip('/'))
    assert (vector, space) == (VECTOR, SPACE_A)
    assert len(calls) == 1


@pytest.mark.parametrize('body', [
    {'faces': []}, {'faces': [], 'embedding_space': None},
    {'faces': [], 'embedding_space': 'private-canary'},
    '{"faces":[],"embedding_space":"' + SPACE_A + '","embedding_space":"' + SPACE_B + '"}',
])
async def test_worker_refuses_unqualified_or_ambiguous_reply_even_with_no_faces(body):
    async def endpoint(request):
        await request.read()
        return web.Response(text=body, content_type='application/json') if isinstance(body, str) else web.json_response(body)
    app = web.Application()
    app.router.add_post('/v1/faces', endpoint)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        worker = SimpleNamespace(_inference_session=session,
            faces={'url': str(server.make_url('/v1/faces'))}, _read_response=JobProcessor._read_response)
        worker._read_response = lambda response, limit: JobProcessor._read_response(worker, response, limit)
        with pytest.raises(WorkerError, match='^Face server returned an invalid reply$'):
            await JobProcessor._detect_faces(worker, b'FICTIONAL', [0, 0, 1, 1])


async def test_worker_keeps_each_response_space_not_shared_mutable_server_state(tmp_path):
    replies = [SPACE_A, SPACE_B]
    async def endpoint(request):
        await request.read()
        return web.json_response({'embedding_space': replies.pop(0), 'faces': [{'embedding': VECTOR}]})
    app = web.Application()
    app.router.add_post('/v1/faces', endpoint)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        worker = SimpleNamespace(_inference_session=session, faces={'url': str(server.make_url('/v1/faces'))})
        worker._read_response = lambda response, limit: JobProcessor._read_response(worker, response, limit)
        first = await JobProcessor._detect_faces(worker, b'FICTIONAL', [0, 0, 1, 1])
        second = await JobProcessor._detect_faces(worker, b'FICTIONAL', [0, 0, 1, 1])
    assert first == ([{'embedding': VECTOR}], SPACE_A)
    assert second == ([{'embedding': VECTOR}], SPACE_B)


@pytest.mark.parametrize("after_first_match", [False, True])
async def test_worker_model_mismatch_never_posts_callback_or_mutates_store(tmp_path, after_first_match):
    store = FaceStore(tmp_path)
    store.enroll('Fictional identity', VECTOR, embedding_space=SPACE_A)
    before = store.path.read_bytes()
    from io import BytesIO
    from PIL import Image
    frame = BytesIO()
    Image.new('RGB', (80, 80), 'gray').save(frame, format='JPEG')
    found = [{'embedding': VECTOR, 'score': .9, 'box': [.1, .1, .5, .5]}]
    replies = [(found, SPACE_A), (found, SPACE_B)] if after_first_match else [(found, SPACE_B)]
    worker = SimpleNamespace(faces={'counts': {'processed': 0}, 'store': store},
        _fetch=AsyncMock(return_value=(b'FICTIONAL-VIDEO', {})),
        _video_frame=AsyncMock(return_value=frame.getvalue()),
        _detect_faces=AsyncMock(side_effect=replies),
        _post_callback=AsyncMock())
    job = SimpleNamespace(payload={'_faces': [(1, 1, [100, 100, 200, 200], None)] * len(replies)},
                          media=[('video', 'https://fictional.invalid/')])
    with pytest.raises(WorkerError, match='^Face server returned an invalid face$'):
        await JobProcessor._execute_faces(worker, job)
    worker._post_callback.assert_not_called()
    assert store.path.read_bytes() == before


async def test_unqualified_analyzer_is_not_labelled_by_default():
    async with TestClient(TestServer(build_app(lambda *_: []))) as client:
        form = FormData()
        form.add_field('image', b'\xff\xd8\xff' + b'FICTIONAL-JPEG' * 6, filename='fixture.jpg', content_type='image/jpeg')
        assert await (await client.post('/v1/faces', data=form)).json() == {'faces': []}


def test_model_digest_uses_bytes_not_path(tmp_path):
    from aikey.face_server import _model_digest
    first, second = tmp_path / 'one', tmp_path / 'two'
    first.write_bytes(b'FICTIONAL-GRAPH-A')
    second.write_bytes(first.read_bytes())
    assert _model_digest(first) == _model_digest(second) == hashlib.sha256(first.read_bytes()).hexdigest()
    second.write_bytes(b'FICTIONAL-GRAPH-B')
    assert _model_digest(first) != _model_digest(second)


@pytest.mark.parametrize('phase', ['detector_before', 'detector_during', 'recognizer_loading'])
def test_opencv_adapter_refuses_changed_model_bytes_without_real_inference(tmp_path, monkeypatch, phase):
    import sys
    import numpy
    from aikey.face_server import ImageError, opencv_analyzer
    detector, recognizer = tmp_path / 'detector', tmp_path / 'recognizer'
    detector.write_bytes(b'FICTIONAL-DETECTOR')
    recognizer.write_bytes(b'FICTIONAL-RECOGNIZER')
    def load_recognizer(*_):
        if phase == 'recognizer_loading':
            recognizer.write_bytes(b'FICTIONAL-REPLACEMENT')
        return SimpleNamespace()
    def detect(_):
        if phase == 'detector_during':
            detector.write_bytes(b'FICTIONAL-REPLACEMENT')
        return None, None
    fake_cv2 = SimpleNamespace(__version__='fictional-1', IMREAD_COLOR=1,
        FaceRecognizerSF=SimpleNamespace(create=load_recognizer),
        FaceDetectorYN=SimpleNamespace(create=lambda *_: SimpleNamespace(detect=detect)),
        imdecode=lambda *_: numpy.zeros((64, 64, 3)))
    monkeypatch.setitem(sys.modules, 'cv2', fake_cv2)
    if phase == 'recognizer_loading':
        with pytest.raises(ImageError, match='^Face model changed during loading$'):
            opencv_analyzer(str(detector), str(recognizer))
    else:
        analyze = opencv_analyzer(str(detector), str(recognizer))
        assert len(analyze.embedding_space) == 64
        if phase == 'detector_before':
            detector.write_bytes(b'FICTIONAL-REPLACEMENT')
        with pytest.raises(ImageError, match='^Face model changed (after loading|during analysis)$'):
            analyze(b'FICTIONAL', None)


@pytest.mark.parametrize('raw_space', [None, True, '', 'a' * 63, 'A' * 64, ['a' * 64]])
def test_invalid_saved_space_is_refused_without_repair_or_label_disclosure(tmp_path, raw_space, capsys):
    store = FaceStore(tmp_path)
    store.enroll('Fictional private canary', VECTOR, embedding_space=SPACE_A)
    raw = json.loads(store.path.read_bytes())
    raw['embedding_space'] = raw_space
    store.path.write_text(json.dumps(raw))
    before = store.path.read_bytes()
    with pytest.raises(FaceStoreError, match='^face_store_invalid$'):
        store.match(VECTOR, embedding_space=SPACE_A)
    assert store.path.read_bytes() == before
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('known', [True, False])
async def test_worker_bound_match_and_unknown_keep_native_payload_and_journal_boundary(tmp_path, known):
    from io import BytesIO
    from PIL import Image
    picture = BytesIO()
    Image.new('RGB', (80, 80), 'gray').save(picture, format='JPEG')
    store = FaceStore(tmp_path)
    store.enroll('Fictional identity', VECTOR, embedding_space=SPACE_A)
    probe = VECTOR if known else [0.0, 1.0] + [0.0] * 126
    callback = AsyncMock(return_value={'status': 'processed'})
    worker = SimpleNamespace(faces={'counts': {'processed': 0}, 'store': store},
        _fetch=AsyncMock(return_value=(b'FICTIONAL-VIDEO', {})),
        _video_frame=AsyncMock(return_value=picture.getvalue()),
        _detect_faces=AsyncMock(return_value=([{'embedding': probe, 'score': .9,
                                               'box': [.1, .1, .5, .5]}], SPACE_A)),
        _post_callback=callback, callback_mode='post')
    job = SimpleNamespace(payload={'_faces': [(1, 1, [100, 100, 200, 200], None)],
        'camera': 'fictional-camera', 'event': 'fictional-event'},
        media=[('video', 'https://fictional.invalid/')])
    result = await JobProcessor._execute_faces(worker, job)
    assert result == {'status': 'processed', 'result': {'faces': 1, 'matched': int(known)}}
    payload = callback.call_args.args[1]
    attrs = payload['faceAttrs']['1']
    assert attrs['matchedName'] == ('Fictional identity' if known else '')
    assert attrs['namesTopK'] == (['Fictional identity'] if known else [])
    assert 'embedding_space' not in json.dumps(payload)
    assert callback.call_args.kwargs['images'][0][1].startswith(b'\xff\xd8\xff')


async def test_cli_enrollment_carries_server_space_without_manual_relabelling(tmp_path, capsys):
    from aikey.faces import main
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    photo = tmp_path / 'fictional.jpg'
    photo.write_bytes(b'\xff\xd8\xff' + b'FICTIONAL' * 8)
    def analyze(*_):
        return [((.1, .1, .4, .4), .9, VECTOR)]
    async with TestServer(build_app(analyze, embedding_space=SPACE_A)) as server:
        assert await asyncio.to_thread(main, ['enroll', '--state-dir', str(state),
            '--image', str(photo), '--name', 'Fictional identity',
            '--server', str(server.make_url('')).rstrip('/')]) == 0
    captured = capsys.readouterr()
    assert captured.err == '' and json.loads(captured.out) == {'templates': 1}
    assert json.loads(FaceStore(state).path.read_bytes())['embedding_space'] == SPACE_A

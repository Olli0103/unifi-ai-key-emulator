"""Fictional CLI/model doubles and temporary kernel locks only."""

import asyncio
import json
from pathlib import Path
import threading

import pytest

from aikey import faces
from aikey.model_work_fence import FILENAME, ModelWorkFence, initialize

VECTOR, SPACE = [1.0] + [0.0] * 127, 'a' * 64


def arguments(root):
    photo = root / 'fictional.jpg'
    photo.write_bytes(b'FICTIONAL-IMAGE')
    return ['enroll', '--state-dir', str(root), '--name', 'Fictional identity', '--image', str(photo)]


def test_cli_holds_existing_shared_fence_during_synchronous_model_request(tmp_path, monkeypatch, capsys):
    initialize(tmp_path, '0' * 32)
    before = (tmp_path / FILENAME).read_bytes()
    observer = ModelWorkFence.from_state(tmp_path)
    calls = []
    def model(*_):
        assert observer.try_exclusive() is None
        calls.append(True)
        return list(VECTOR), SPACE
    monkeypatch.setattr(faces, 'embed_single_face', model)
    try:
        assert faces.main(arguments(tmp_path)) == 0
        assert calls == [True]
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
        assert (tmp_path / FILENAME).read_bytes() == before
        assert faces.FaceStore(tmp_path).match(VECTOR, embedding_space=SPACE)[0] == 'Fictional identity'
        assert json.loads(capsys.readouterr().out) == {'templates': 1}
    finally:
        observer.close()


def test_cli_waits_for_writer_without_reading_image_or_calling_model(tmp_path, monkeypatch, capsys):
    from concurrent.futures import ThreadPoolExecutor
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    writer = observer.try_exclusive()
    calls = []
    def model(*_):
        calls.append(True)
        assert observer.try_exclusive() is None
        return list(VECTOR), SPACE
    monkeypatch.setattr(faces, 'embed_single_face', model)
    entered = threading.Event()
    acquire = ModelWorkFence.acquire
    async def observed(self, **kwargs):
        entered.set()
        return await acquire(self, **kwargs)
    monkeypatch.setattr(ModelWorkFence, 'acquire', observed)
    args = arguments(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(faces.main, args)
            try:
                assert entered.wait(2)
                assert calls == [] and not faces.FaceStore(tmp_path).path.exists()
            finally:
                writer.close()
            assert result.result(timeout=3) == 0
        assert calls == [True] and capsys.readouterr().err == ''
    finally:
        writer.close()
        observer.close()


@pytest.mark.parametrize('failure', [faces.FaceStoreError('enrollment_unavailable'), KeyboardInterrupt()])
def test_cli_model_failure_or_keyboard_interrupt_releases_owned_fence_without_saving(tmp_path, monkeypatch, failure):
    initialize(tmp_path, '0' * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    def broken(*_):
        assert observer.try_exclusive() is None
        raise failure
    monkeypatch.setattr(faces, 'embed_single_face', broken)
    try:
        if isinstance(failure, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                faces.main(arguments(tmp_path))
        else:
            assert faces.main(arguments(tmp_path)) == 2
        assert not faces.FaceStore(tmp_path).path.exists()
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        observer.close()


@pytest.mark.parametrize('kind', ['malformed', 'symlink', 'permissive'])
def test_bad_existing_marker_never_disables_fencing_or_contacts_model(tmp_path, monkeypatch, capsys, kind):
    marker = tmp_path / FILENAME
    if kind == 'malformed':
        marker.write_bytes(b'FICTIONAL-BAD-MARKER')
        marker.chmod(0o600)
    elif kind == 'symlink':
        target = tmp_path / 'fictional-external-marker'
        target.write_bytes(b'FICTIONAL-CANARY')
        marker.symlink_to(target)
    else:
        initialize(tmp_path, '0' * 32)
        marker.chmod(0o644)
    before = marker.read_bytes()
    calls = []
    monkeypatch.setattr(faces, 'embed_single_face', lambda *_: calls.append(True))
    assert faces.main(arguments(tmp_path)) == 2
    assert calls == [] and not faces.FaceStore(tmp_path).path.exists()
    assert marker.read_bytes() == before
    output = capsys.readouterr()
    assert output.out == '' and 'FICTIONAL' not in output.err and str(tmp_path) not in output.err


async def test_cli_expired_wait_refuses_without_image_read_or_model_call(tmp_path, monkeypatch, capsys):
    initialize(tmp_path, '0' * 32)
    args = arguments(tmp_path)
    Path(args[-1]).unlink()
    calls = []
    async def expired(self, **kwargs):
        raise TimeoutError('model_work_fence_wait_expired')
    monkeypatch.setattr(ModelWorkFence, 'acquire', expired)
    monkeypatch.setattr(faces, 'embed_single_face', lambda *_: calls.append(True))
    assert await asyncio.to_thread(faces.main, args) == 2
    assert calls == [] and 'model_work_fence_wait_expired' in capsys.readouterr().err


def test_absent_marker_preserves_legacy_behavior_without_creating_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(faces, 'embed_single_face', lambda *_: (list(VECTOR), SPACE))
    assert faces.main(arguments(tmp_path)) == 0
    assert not (tmp_path / FILENAME).exists()


@pytest.mark.parametrize('command', ['list', 'delete', 'purge'])
def test_non_model_cli_commands_never_construct_a_model_fence(tmp_path, monkeypatch, command):
    def forbidden(*_):
        raise AssertionError('non-model command consulted model fence')
    monkeypatch.setattr(ModelWorkFence, 'from_state', forbidden)
    assert faces.main([command, '--state-dir', str(tmp_path)]) == 0
    assert not (tmp_path / FILENAME).exists()

"""Explicit fictional photos/vectors and actual local CLI/lock boundaries only."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys

import pytest

from aikey import faces
from aikey.model_work_fence import FILENAME, ModelWorkFence, ModelWorkFenceError, initialize

VECTOR, SPACE = [1.0] + [0.0] * 127, "a" * 64
PRIVATE = "FICTIONAL-PRIVATE-PHOTO-CANARY"


def arguments(root, photo, *, name="Fictional identity"):
    return ["enroll", "--state-dir", str(root), "--name", name, "--image", str(photo)]


def install_model(monkeypatch, calls):
    def model(image, server):
        calls.append((image, server))
        return list(VECTOR), SPACE

    monkeypatch.setattr(faces, "embed_single_face", model)


def test_regular_explicit_photo_preserves_bytes_and_legacy_fence_behavior(
    tmp_path, monkeypatch, capsys
):
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE-BYTES")
    before = photo.read_bytes(), photo.stat().st_mode
    calls = []
    install_model(monkeypatch, calls)
    assert faces.main(arguments(tmp_path, photo)) == 0
    assert calls == [(before[0], "http://127.0.0.1:8179")]
    assert (photo.read_bytes(), photo.stat().st_mode) == before
    assert not (tmp_path / FILENAME).exists()
    assert faces.FaceStore(tmp_path).match(VECTOR, embedding_space=SPACE)[0] == "Fictional identity"
    assert json.loads(capsys.readouterr().out) == {"templates": 1}


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "empty", "oversized"])
def test_bad_explicit_photo_never_reaches_model_or_store_and_has_fixed_error(
    tmp_path, monkeypatch, capsys, kind
):
    initialize(tmp_path, "0" * 32)
    marker = (tmp_path / FILENAME).read_bytes()
    photo = tmp_path / (PRIVATE + ".jpg")
    if kind == "directory":
        photo.mkdir()
    elif kind == "symlink":
        target = tmp_path / "fictional-target"
        target.write_bytes(b"FICTIONAL-IMAGE")
        photo.symlink_to(target)
    elif kind == "empty":
        photo.write_bytes(b"")
    elif kind == "oversized":
        with photo.open("wb") as output:
            output.truncate(10 * 1024 * 1024 + 1)
    calls = []
    install_model(monkeypatch, calls)
    assert faces.main(arguments(tmp_path, photo)) == 2
    assert calls == [] and not faces.FaceStore(tmp_path).path.exists()
    captured = capsys.readouterr()
    assert captured.out == "" and PRIVATE not in captured.err and str(tmp_path) not in captured.err
    assert (tmp_path / FILENAME).read_bytes() == marker
    observer = ModelWorkFence.from_state(tmp_path)
    try:
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        observer.close()


@pytest.mark.parametrize("name", [" " * 3, "x" * 65, "fictional\nprivate"])
def test_invalid_identity_label_is_refused_before_fence_image_or_model(
    tmp_path, monkeypatch, capsys, name
):
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    calls = []
    install_model(monkeypatch, calls)

    def fence(*args, **kwargs):
        raise AssertionError("invalid label reached model-work admission")

    monkeypatch.setattr(ModelWorkFence, "from_state", fence)
    assert faces.main(arguments(tmp_path, photo, name=name)) == 2
    assert calls == [] and not faces.FaceStore(tmp_path).path.exists()
    assert capsys.readouterr().out == ""


def test_existing_model_lease_covers_the_real_store_commit(tmp_path, monkeypatch, capsys):
    initialize(tmp_path, "0" * 32)
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    observer = ModelWorkFence.from_state(tmp_path)
    original = faces.FaceStore.enroll
    calls = []
    install_model(monkeypatch, calls)

    def enroll(self, *args, **kwargs):
        writer = observer.try_exclusive()
        if writer is not None:
            writer.close()
        assert writer is None, "model migration acquired exclusivity before template persistence"
        result = original(self, *args, **kwargs)
        writer = observer.try_exclusive()
        if writer is not None:
            writer.close()
        assert writer is None, "lease ended before fsync/readback completed"
        return result

    monkeypatch.setattr(faces.FaceStore, "enroll", enroll)
    try:
        assert faces.main(arguments(tmp_path, photo)) == 0
        assert len(calls) == 1 and faces.FaceStore(tmp_path).path.exists()
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
        assert json.loads(capsys.readouterr().out) == {"templates": 1}
    finally:
        observer.close()


@pytest.mark.parametrize(
    "failure",
    [
        OSError(PRIVATE),
        ValueError(PRIVATE),
        KeyError(PRIVATE),
        faces.FaceStoreError(PRIVATE),
        ModelWorkFenceError(PRIVATE),
    ],
)
def test_unknown_failure_text_cannot_expose_photo_or_identity_data(
    tmp_path, monkeypatch, capsys, failure
):
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")

    def unavailable(*args):
        raise failure

    monkeypatch.setattr(faces, "embed_single_face", unavailable)
    assert faces.main(arguments(tmp_path, photo)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and PRIVATE not in captured.err and str(tmp_path) not in captured.err
    assert re.search(r"faces\.py:[0-9]+", captured.err)
    assert not faces.FaceStore(tmp_path).path.exists()


def test_fifo_photo_is_rejected_by_actual_cli_process_without_waiting_for_a_writer(tmp_path):
    photo = tmp_path / "fictional-photo-fifo"
    os.mkfifo(photo, 0o600)
    script = """import sys\nfrom aikey import faces\ndef forbidden(*args):\n raise AssertionError('non-regular photo reached model')\nfaces.embed_single_face=forbidden\nraise SystemExit(faces.main(['enroll','--state-dir',sys.argv[1],'--name','Fictional identity','--image',sys.argv[2]]))\n"""
    env = dict(os.environ, PYTHONPATH=str(Path(faces.__file__).resolve().parents[1]))
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(photo)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("actual CLI blocked on a FIFO photo; owning child was killed and collected")
    assert result.returncode == 2 and result.stdout == ""
    assert str(tmp_path) not in result.stderr and photo.name not in result.stderr
    assert not faces.FaceStore(tmp_path).path.exists()


@pytest.mark.parametrize("change", ["delete", "purge", "sibling_enrollment"])
@pytest.mark.parametrize("guarded", [False, True])
def test_store_change_during_analysis_cannot_be_reversed_by_a_stale_cli_save(
    tmp_path, monkeypatch, capsys, change, guarded
):
    if guarded:
        initialize(tmp_path, "0" * 32)
    store = faces.FaceStore(tmp_path)
    store.enroll("Fictional identity", list(VECTOR), embedding_space=SPACE)
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    after = []
    calls = []

    def model(*args):
        calls.append(True)
        if change == "delete":
            store.delete("Fictional identity")
        elif change == "purge":
            store.purge()
        else:
            store.enroll("Fictional sibling", list(VECTOR), embedding_space=SPACE)
        after.append(store.path.read_bytes() if store.path.exists() else None)
        return list(VECTOR), SPACE

    monkeypatch.setattr(faces, "embed_single_face", model)
    assert faces.main(arguments(tmp_path, photo)) == 2
    assert calls == [True]
    assert (store.path.read_bytes() if store.path.exists() else None) == after[0]
    captured = capsys.readouterr()
    assert captured.out == "" and "face_store_changed" in captured.err


@pytest.mark.parametrize("kind", ["legacy", "identity_limit", "template_limit"])
def test_known_impossible_store_admission_is_refused_before_photo_analysis(
    tmp_path, monkeypatch, capsys, kind
):
    store = faces.FaceStore(tmp_path)
    if kind == "legacy":
        store.directory.mkdir(mode=0o700)
        store.path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "identities": {
                        "Fictional identity": {"created": 1, "templates": [list(VECTOR)]}
                    },
                }
            )
        )
        store.path.chmod(0o600)
    elif kind == "identity_limit":
        for number in range(faces._MAX_IDENTITIES):
            store.enroll("Fictional " + str(number), list(VECTOR), embedding_space=SPACE)
    else:
        for _ in range(faces._MAX_TEMPLATES):
            store.enroll("Fictional identity", list(VECTOR), embedding_space=SPACE)
    before = store.path.read_bytes()
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    calls = []
    install_model(monkeypatch, calls)
    assert faces.main(arguments(tmp_path, photo)) == 2
    assert calls == [] and store.path.read_bytes() == before
    captured = capsys.readouterr()
    assert captured.out == "" and "Fictional" not in captured.err


@pytest.mark.parametrize("change", ["grow", "shrink"])
def test_photo_changing_during_bounded_descriptor_read_is_not_analyzed(
    tmp_path, monkeypatch, capsys, change
):
    photo = tmp_path / "fictional-photo"
    original = b"FICTIONAL-IMAGE-BYTES-123456789012"
    photo.write_bytes(original)
    identity = (photo.stat().st_dev, photo.stat().st_ino)
    monkeypatch.setattr(faces, "_MAX_ENROLLMENT_IMAGE_BYTES", 64)
    fdopen = os.fdopen
    requests = []
    changed = original + b"changed" if change == "grow" else b"x"

    class ChangingPhoto:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            requests.append(size)
            raw = self.stream.read(size)
            photo.write_bytes(changed)
            return raw

    def opened(fd, *args, **kwargs):
        info = os.fstat(fd)
        stream = fdopen(fd, *args, **kwargs)
        return ChangingPhoto(stream) if (info.st_dev, info.st_ino) == identity else stream

    monkeypatch.setattr(faces.os, "fdopen", opened)
    calls = []
    install_model(monkeypatch, calls)
    assert faces.main(arguments(tmp_path, photo)) == 2
    assert requests and all(0 < size <= 65 for size in requests)
    assert calls == [] and not faces.FaceStore(tmp_path).path.exists()
    assert photo.read_bytes() == changed
    captured = capsys.readouterr()
    assert captured.out == "" and str(tmp_path) not in captured.err


def test_post_commit_failure_stays_uncertain_without_undo_or_repeat(tmp_path, monkeypatch, capsys):
    initialize(tmp_path, "0" * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    original = faces.FaceStore._save
    calls = []
    install_model(monkeypatch, calls)

    def saved_then_uncertain(self, *args, **kwargs):
        original(self, *args, **kwargs)
        writer = observer.try_exclusive()
        if writer is not None:
            writer.close()
        assert writer is None
        raise faces.FaceStoreOutcomeUncertain("face_store_write_uncertain")

    monkeypatch.setattr(faces.FaceStore, "_save", saved_then_uncertain)
    try:
        assert faces.main(arguments(tmp_path, photo)) == 2
        assert len(calls) == 1
        assert (
            faces.FaceStore(tmp_path).match(VECTOR, embedding_space=SPACE)[0]
            == "Fictional identity"
        )
        captured = capsys.readouterr()
        assert captured.out == "" and "face_store_write_uncertain" in captured.err
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        observer.close()


def test_interrupt_during_save_collects_the_owned_model_lease(tmp_path, monkeypatch):
    initialize(tmp_path, "0" * 32)
    observer = ModelWorkFence.from_state(tmp_path)
    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")
    calls = []
    install_model(monkeypatch, calls)

    def interrupted(self, *args, **kwargs):
        writer = observer.try_exclusive()
        if writer is not None:
            writer.close()
        assert writer is None
        raise KeyboardInterrupt

    monkeypatch.setattr(faces.FaceStore, "enroll", interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            faces.main(arguments(tmp_path, photo))
        assert len(calls) == 1 and not faces.FaceStore(tmp_path).path.exists()
        writer = observer.try_exclusive()
        assert writer is not None
        writer.close()
    finally:
        observer.close()


@pytest.mark.parametrize("kind", ["unknown_command", "unknown_option", "extra_argument"])
def test_argument_parser_never_echoes_private_values(tmp_path, monkeypatch, capsys, kind):
    import uuid

    canary = "FICTIONAL-PRIVATE-" + uuid.uuid4().hex
    photo = tmp_path / (canary + ".jpg")
    args = arguments(tmp_path, photo, name=canary)
    if kind == "unknown_command":
        args[0] = canary
    elif kind == "unknown_option":
        args.append("--" + canary)
    else:
        args.append(str(tmp_path / canary))

    def forbidden(*args, **kwargs):
        raise AssertionError("malformed CLI reached admission")

    monkeypatch.setattr(ModelWorkFence, "from_state", forbidden)
    with pytest.raises(SystemExit) as error:
        faces.main(args)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and canary not in captured.err and str(tmp_path) not in captured.err


def test_unknown_exception_formatter_is_not_invoked(tmp_path, monkeypatch, capsys):
    class PrivateFailure(ValueError):
        def __str__(self):
            raise AssertionError("private formatter was invoked")

    photo = tmp_path / "fictional-photo"
    photo.write_bytes(b"FICTIONAL-IMAGE")

    def unavailable(*args):
        raise PrivateFailure(PRIVATE)

    monkeypatch.setattr(faces, "embed_single_face", unavailable)
    assert faces.main(arguments(tmp_path, photo)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and PRIVATE not in captured.err
    assert re.search(r"faces\.py:[0-9]+", captured.err)
    assert not faces.FaceStore(tmp_path).path.exists()

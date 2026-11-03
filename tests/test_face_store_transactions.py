"""Synthetic local template transactions only, no photos/models/real identities."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from aikey.faces import FaceStore, FaceStoreError
from test_face_store_boundary import TEMPLATE, record


def test_concurrent_enrollments_cannot_silently_erase_another_identity(tmp_path, monkeypatch):
    first, second = FaceStore(tmp_path), FaceStore(tmp_path)
    entered, second_loaded, release = threading.Event(), threading.Event(), threading.Event()
    load_first, load_second = first._load, second._load
    def paused(*args, **kwargs):
        result = load_first(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result
    def observed(*args, **kwargs):
        result = load_second(*args, **kwargs)
        second_loaded.set()
        return result
    monkeypatch.setattr(first, '_load', paused)
    monkeypatch.setattr(second, '_load', observed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.enroll, 'Fictional first identity', TEMPLATE, embedding_space='a'*64)
        assert entered.wait(3)
        b = pool.submit(second.enroll, 'Fictional second identity', TEMPLATE, embedding_space='a'*64)
        # Old unguarded second reader sees empty state while the first is paused.
        # A serialized reader instead waits until the first commits.
        second_loaded.wait(.2)
        release.set()
        assert a.result(timeout=6) == b.result(timeout=6) == 1
    assert FaceStore(tmp_path).names() == ['Fictional first identity', 'Fictional second identity']


@pytest.mark.parametrize('operation', ['read', 'enroll', 'purge'])
def test_symlinked_faces_directory_never_reads_writes_or_purges_outside_state(tmp_path, operation):
    state, outside = tmp_path / 'state', tmp_path / 'outside'
    state.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    canary = outside / 'identities.json'
    canary.write_text(json.dumps(record()))
    canary.chmod(0o600)
    before = canary.read_bytes()
    (state / 'faces').symlink_to(outside, target_is_directory=True)
    store = FaceStore(state)
    with pytest.raises(FaceStoreError):
        if operation == 'read':
            store.names()
        elif operation == 'enroll':
            store.enroll('Fictional added identity', TEMPLATE, embedding_space='a' * 64)
        else:
            store.purge()
    assert canary.read_bytes() == before and outside.stat().st_mode & 0o777 == 0o700
    assert (state / 'faces').is_symlink()


@pytest.mark.parametrize('operation', ['enroll', 'delete', 'purge'])
def test_optional_revision_prevents_stale_identity_mutation(tmp_path, operation):
    from aikey.faces import FaceStoreRevisionConflict
    store = FaceStore(tmp_path)
    store.enroll('Fictional existing identity', TEMPLATE, embedding_space='a' * 64)
    revision = store.snapshot()['revision']
    store.enroll('Fictional concurrent identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    with pytest.raises(FaceStoreRevisionConflict, match='^face_store_changed$'):
        if operation == 'enroll':
            store.enroll('Fictional stale identity', TEMPLATE, expected_revision=revision, embedding_space='a' * 64)
        elif operation == 'delete':
            store.delete('Fictional existing identity', expected_revision=revision)
        else:
            store.purge(expected_revision=revision)
    assert store.path.read_bytes() == before


def test_snapshot_uses_exact_bytes_and_has_metadata_not_vectors(tmp_path, capsys):
    import hashlib
    store = FaceStore(tmp_path)
    missing = store.snapshot()
    assert missing['identities'] == [] and list(tmp_path.iterdir()) == []
    assert store.enroll('Fictional identity', TEMPLATE, expected_revision=missing['revision'], embedding_space='a' * 64) == 1
    status = store.snapshot()
    assert status['revision'] == hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert status['identities'][0]['name'] == 'Fictional identity'
    assert status['identities'][0]['templates'] == 1
    assert isinstance(status['identities'][0]['created'], int)
    assert 'embedding' not in json.dumps(status) and '[1.0,' not in json.dumps(status)
    store.delete('Fictional identity', expected_revision=status['revision'])
    assert store.snapshot()['revision'] != missing['revision']
    store.purge(expected_revision=store.snapshot()['revision'])
    assert store.snapshot() == missing
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('fault', ['faces_mode', 'root_writable', 'store_hardlink', 'lock_symlink', 'lock_fifo', 'lock_mode'])
def test_unsafe_authority_is_not_repaired_or_used(tmp_path, fault):
    import os
    store = FaceStore(tmp_path)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    if fault == 'faces_mode':
        store.directory.chmod(0o755)
    elif fault == 'root_writable':
        tmp_path.chmod(0o777)
    elif fault == 'store_hardlink':
        os.link(store.path, tmp_path / 'retained-hardlink')
    else:
        lock = tmp_path / '.face-store.lock'
        lock.unlink()
        if fault == 'lock_symlink':
            target = tmp_path / 'retained-lock-canary'
            target.write_bytes(b'fictional lock canary')
            lock.symlink_to(target)
        elif fault == 'lock_fifo':
            os.mkfifo(lock, 0o600)
        else:
            lock.write_bytes(b'')
            lock.chmod(0o644)
    try:
        with pytest.raises(FaceStoreError):
            store.enroll('Fictional refused identity', TEMPLATE, embedding_space='a' * 64)
        assert store.path.read_bytes() == before
        if fault == 'faces_mode':
            assert store.directory.stat().st_mode & 0o777 == 0o755
        if fault == 'lock_symlink':
            assert target.read_bytes() == b'fictional lock canary' and lock.is_symlink()
    finally:
        tmp_path.chmod(0o700)
        if store.directory.exists():
            store.directory.chmod(0o700)


@pytest.mark.parametrize('at', ['file_fsync', 'directory_fsync'])
def test_write_uncertainty_keeps_committed_or_prior_bytes_without_retry(tmp_path, monkeypatch, at):
    import os
    import stat
    from aikey.faces import FaceStoreOutcomeUncertain
    from aikey import faces
    store = FaceStore(tmp_path)
    store.enroll('Fictional original identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    actual = os.fsync
    failures = []
    def fail(fd):
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
        if regular == (at == 'file_fsync'):
            failures.append(True)
            raise OSError('CANARY-private-filesystem-detail')
        return actual(fd)
    monkeypatch.setattr(faces.os, 'fsync', fail)
    with pytest.raises(FaceStoreOutcomeUncertain, match='^face_store_write_uncertain$'):
        store.enroll('Fictional new identity', TEMPLATE, embedding_space='a' * 64)
    assert failures == [True]
    assert (store.path.read_bytes() == before) is (at == 'file_fsync')
    assert not list(store.directory.glob('.faces-*'))
    if at == 'directory_fsync':
        assert len(store.names()) == 2


def test_directory_swap_cannot_redirect_a_write_to_external_canary(tmp_path, monkeypatch):
    import os
    from aikey import faces
    from aikey.faces import FaceStoreOutcomeUncertain
    root, outside = tmp_path / 'state', tmp_path / 'outside'
    root.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    canary = outside / 'identities.json'
    canary.write_bytes(b'fictional external canary')
    canary.chmod(0o600)
    store = FaceStore(root)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    actual = os.replace
    retained = root / 'retained-faces'
    def exchange(*args, **kwargs):
        store.directory.rename(retained)
        store.directory.symlink_to(outside, target_is_directory=True)
        return actual(*args, **kwargs)
    monkeypatch.setattr(faces.os, 'replace', exchange)
    with pytest.raises(FaceStoreOutcomeUncertain, match='^face_store_write_uncertain$'):
        store.enroll('Fictional added identity', TEMPLATE, embedding_space='a' * 64)
    assert canary.read_bytes() == b'fictional external canary'
    # The original descriptor may already have committed. Preserve and report it.
    data = json.loads((retained / 'identities.json').read_bytes())
    assert len(data['identities']) == 2 and store.directory.is_symlink()


def test_existing_unowned_temporary_is_not_removed_on_name_collision(tmp_path, monkeypatch):
    from aikey import faces
    from aikey.faces import FaceStoreOutcomeUncertain
    store = FaceStore(tmp_path)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    canary = store.directory / '.faces-collision'
    canary.write_bytes(b'fictional temporary canary')
    monkeypatch.setattr(faces.secrets, 'token_hex', lambda _: 'collision')
    with pytest.raises(FaceStoreOutcomeUncertain):
        store.enroll('Fictional refused identity', TEMPLATE, embedding_space='a' * 64)
    assert store.path.read_bytes() == before and canary.read_bytes() == b'fictional temporary canary'


def test_real_processes_share_one_identity_writer_without_lost_templates(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    code = '''
from pathlib import Path
import sys,time
from aikey.faces import FaceStore
root=Path(sys.argv[1]); ready=Path(sys.argv[2]); go=Path(sys.argv[3])
ready.write_text('ready')
deadline=time.monotonic()+8
while not go.exists():
    if time.monotonic()>deadline: raise SystemExit(7)
    time.sleep(.01)
store=FaceStore(root)
for _ in range(5): store.enroll('Fictional shared identity',[1.0]+[0.0]*127, embedding_space='a'*64)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    children = []
    go = tmp_path / 'start'
    try:
        for i in range(2):
            ready = tmp_path / f'ready-{i}'
            children.append(subprocess.Popen([sys.executable, '-c', code, str(tmp_path), str(ready), str(go)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        deadline = time.monotonic() + 5
        while not all((tmp_path / f'ready-{i}').exists() for i in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        go.write_text('go')
        for child in children:
            stdout, stderr = child.communicate(timeout=8)
            assert child.returncode == 0 and stdout == stderr == b'', stderr.decode(errors='replace')
        assert FaceStore(tmp_path).snapshot()['identities'][0]['templates'] == 10
        assert len(FaceStore(tmp_path).names()) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=3)


def test_busy_writer_has_a_real_bound_and_keeps_the_lock_inode(tmp_path):
    import fcntl
    import os
    import time
    store = FaceStore(tmp_path)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    lock = tmp_path / '.face-store.lock'
    inode = lock.stat().st_ino
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with pytest.raises(FaceStoreError, match='^face_store_busy$'):
            FaceStore(tmp_path).delete('Fictional retained identity')
        assert 4.5 <= time.monotonic() - started < 6
        assert store.path.read_bytes() == before and lock.stat().st_ino == inode
    finally:
        os.close(fd)
    assert store.delete('Fictional retained identity')


def test_missing_state_root_is_not_silently_created_for_enrollment(tmp_path):
    missing = tmp_path / 'missing-state'
    store = FaceStore(missing)
    assert store.snapshot()['identities'] == [] and not missing.exists()
    with pytest.raises(FaceStoreError, match='^face_store_state_root_missing$'):
        store.enroll('Fictional refused identity', TEMPLATE, embedding_space='a' * 64)
    assert not missing.exists()


def test_explicit_purge_can_remove_corrupt_owned_record_but_not_an_alias(tmp_path):
    store = FaceStore(tmp_path)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    store.path.write_bytes(b'fictional corrupt JSON')
    with pytest.raises(FaceStoreError):
        store.snapshot()
    store.purge()
    assert not store.path.exists() and store.snapshot()['identities'] == []


def test_success_acknowledgement_requires_exact_store_readback(tmp_path, monkeypatch):
    import os
    from aikey import faces
    from aikey.faces import FaceStoreOutcomeUncertain
    store = FaceStore(tmp_path)
    store.enroll('Fictional original identity', TEMPLATE, embedding_space='a' * 64)
    actual = os.replace
    calls = []
    def changed(*args, **kwargs):
        calls.append(True)
        result = actual(*args, **kwargs)
        store.path.write_bytes(b'fictional changed record')
        return result
    monkeypatch.setattr(faces.os, 'replace', changed)
    with pytest.raises(FaceStoreOutcomeUncertain, match='^face_store_write_uncertain$'):
        store.enroll('Fictional new identity', TEMPLATE, embedding_space='a' * 64)
    assert calls == [True] and store.path.read_bytes() == b'fictional changed record'


def test_purge_does_not_claim_success_when_unlink_has_no_persisted_effect(tmp_path, monkeypatch):
    from aikey import faces
    from aikey.faces import FaceStoreOutcomeUncertain
    store = FaceStore(tmp_path)
    store.enroll('Fictional retained identity', TEMPLATE, embedding_space='a' * 64)
    before = store.path.read_bytes()
    calls = []
    def unacknowledged(*args, **kwargs):
        calls.append(True)
    monkeypatch.setattr(faces.os, 'unlink', unacknowledged)
    with pytest.raises(FaceStoreOutcomeUncertain, match='^face_store_write_uncertain$'):
        store.purge()
    assert calls == [True] and store.path.read_bytes() == before

"""Actual file locks across processes; no health-based stopped assumption."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aikey.index_authority import IndexAuthority, IndexAuthorityError, LOCK, MARKER


def test_legacy_state_never_counts_as_stopped_and_is_not_bootstrapped_by_migration(tmp_path):
    with pytest.raises(IndexAuthorityError):
        IndexAuthority(tmp_path)
    assert not list(tmp_path.iterdir())


def test_live_runtime_excludes_migration_and_stop_releases_exact_same_lock(tmp_path):
    with IndexAuthority(tmp_path, runtime=True) as runtime:
        marker = (tmp_path/MARKER).read_bytes()
        inode = (tmp_path/LOCK).stat().st_ino
        with pytest.raises(IndexAuthorityError, match='holds index authority'):
            IndexAuthority(tmp_path)
        assert runtime.check()
    with IndexAuthority(tmp_path) as migration:
        assert migration.check()
        with pytest.raises(IndexAuthorityError, match='holds index authority'):
            IndexAuthority(tmp_path, runtime=True)
    assert (tmp_path/LOCK).stat().st_ino == inode
    assert (tmp_path/MARKER).read_bytes() == marker


def test_a_separate_process_cannot_restart_key_during_migration(tmp_path):
    with IndexAuthority(tmp_path, runtime=True):
        pass
    with IndexAuthority(tmp_path):
        code = '''from pathlib import Path
import sys
from aikey.index_authority import IndexAuthority,IndexAuthorityError
try: IndexAuthority(Path(sys.argv[1]),runtime=True)
except IndexAuthorityError: print('refused')
else: raise SystemExit('unsafe acquisition')
'''
        r = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src')),
            capture_output=True, text=True, timeout=5)
        assert r.returncode == 0 and r.stdout.strip() == 'refused'
    with IndexAuthority(tmp_path, runtime=True) as runtime:
        assert runtime.check()


@pytest.mark.parametrize('change', ['lock-replace', 'root-replace', 'marker', 'mode', 'hardlink'])
def test_replaced_or_weakened_authority_is_not_a_quiescence_receipt(tmp_path, change):
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    with IndexAuthority(state, runtime=True):
        pass
    with IndexAuthority(state) as migration:
        if change == 'lock-replace':
            (state/LOCK).rename(state/'old-lock')
            (state/LOCK).touch(mode=0o600)
        elif change == 'root-replace':
            state.rename(tmp_path/'retained-state')
            state.mkdir(mode=0o700)
        elif change == 'marker':
            (state/MARKER).write_text('{}')
        elif change == 'mode':
            (state/LOCK).chmod(0o644)
        elif change == 'hardlink':
            os.link(state/LOCK, state/'extra-lock')
        with pytest.raises(IndexAuthorityError):
            migration.check()


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'hardlink', 'world-writable'])
def test_unsafe_lock_members_refuse_before_marker_publication(tmp_path, kind):
    outside = tmp_path/'outside'
    outside.write_bytes(b'outside-canary')
    outside.chmod(0o600)
    lock = tmp_path/LOCK
    if kind == 'symlink':
        lock.symlink_to(outside)
    elif kind == 'fifo':
        os.mkfifo(lock, 0o600)
    elif kind == 'hardlink':
        os.link(outside, lock)
    else:
        lock.touch(mode=0o666)
        lock.chmod(0o666)
    with pytest.raises(IndexAuthorityError):
        IndexAuthority(tmp_path, runtime=True)
    assert outside.read_bytes() == b'outside-canary' and not (tmp_path/MARKER).exists()


def test_marker_cannot_be_reused_with_another_lock_inode(tmp_path):
    with IndexAuthority(tmp_path, runtime=True):
        pass
    marker = json.loads((tmp_path/MARKER).read_text())
    (tmp_path/LOCK).rename(tmp_path/'retained-lock')
    (tmp_path/LOCK).touch(mode=0o600)
    assert (tmp_path/LOCK).stat().st_ino != marker['lock_inode']
    with pytest.raises(IndexAuthorityError, match='participating runtime authority'):
        IndexAuthority(tmp_path)

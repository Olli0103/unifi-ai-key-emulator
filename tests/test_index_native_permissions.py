"""NAS-like inherited mode must be narrowed before private payload writes."""
import os
import stat

from aikey import index_rebuild, index_source_export


def test_atomic_record_narrows_inherited_mode(tmp_path, monkeypatch):
    original = index_rebuild.tempfile.mkstemp
    def inherited(*args, **kwargs):
        fd, name = original(*args, **kwargs)
        os.fchmod(fd, 0o777)
        return fd, name
    monkeypatch.setattr(index_rebuild.tempfile, 'mkstemp', inherited)
    path = tmp_path/'record.json'
    index_rebuild._atomic_json(path, {'private': 'fictional'})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert index_rebuild.Rebuild._read(path) == {'private': 'fictional'}


def test_export_writer_narrows_inherited_mode(tmp_path, monkeypatch):
    original = os.open
    directory = original(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    def inherited(path, flags, *args, **kwargs):
        fd = original(path, flags, *args, **kwargs)
        if flags & os.O_CREAT:
            os.fchmod(fd, 0o777)
        return fd
    monkeypatch.setattr(os, 'open', inherited)
    try:
        index_source_export._write(directory, 'fixture', b'fictional')
    finally:
        os.close(directory)
    assert stat.S_IMODE((tmp_path/'fixture').stat().st_mode) == 0o600
    assert (tmp_path/'fixture').read_bytes() == b'fictional'

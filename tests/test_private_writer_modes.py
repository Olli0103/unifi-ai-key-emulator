"""Reproduce NAS creation-mode overrides without changing existing directories."""

import os
import stat

import pytest

from aikey.config import atomic_private


def test_private_bytes_follow_verified_file_hardening_not_requested_mode(tmp_path, monkeypatch):
    parent = tmp_path / "retained-parent"
    parent.mkdir(mode=0o750)
    parent.chmod(0o750)
    target = parent / "saved"
    original_open, original_fchmod = os.open, os.fchmod
    checked = []

    def nas_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if flags & os.O_EXCL:
            original_fchmod(fd, 0o666)
        return fd

    def harden(fd, mode):
        info = os.fstat(fd)
        assert stat.S_ISREG(info.st_mode), "Directory fsync descriptor must not be chmodded"
        assert info.st_size == 0, "No private bytes before permissions are enforced"
        checked.append(mode)
        original_fchmod(fd, mode)

    monkeypatch.setattr(os, "open", nas_open)
    monkeypatch.setattr(os, "fchmod", harden)
    atomic_private(target, b"fictional-private-content")
    assert checked == [0o600]
    assert target.read_bytes() == b"fictional-private-content"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(parent.stat().st_mode) == 0o750
    assert list(parent.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["exception", "ineffective"])
def test_hardening_refusal_keeps_old_destination_and_removes_empty_temp(tmp_path, monkeypatch, failure):
    target = tmp_path / "saved"
    target.write_bytes(b"retained-old-content")
    original_open, original_fchmod = os.open, os.fchmod

    def nas_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if flags & os.O_EXCL:
            original_fchmod(fd, 0o666)
        return fd

    def refuse(fd, mode):
        assert os.fstat(fd).st_size == 0
        if failure == "exception":
            raise PermissionError("fictional hardening failure")

    monkeypatch.setattr(os, "open", nas_open)
    monkeypatch.setattr(os, "fchmod", refuse)
    with pytest.raises((PermissionError, ValueError)):
        atomic_private(target, b"must-never-be-written")
    assert target.read_bytes() == b"retained-old-content"
    assert list(tmp_path.iterdir()) == [target]

"""Wrong administrator-file types must refuse startup, not block on a writer."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from aikey.control_site import _private_bytes


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes required")
@pytest.mark.parametrize("filename", ["admin-signing-key", "admin-password-record"])
def test_cli_refuses_administrator_fifo_without_waiting_for_a_writer(tmp_path, filename):
    state = tmp_path / "admin"
    state.mkdir(mode=0o700)
    (state / "admin-signing-key").write_bytes(b"s" * 32)
    (state / "admin-password-record").write_text("synthetic-record-not-a-secret")
    for path in state.iterdir():
        path.chmod(0o600)
    selected = state / filename
    selected.unlink()
    os.mkfifo(selected, 0o600)
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "aikey.control_site", "run",
             "--state-dir", str(state), "--aikey-config", str(tmp_path / "absent.json")],
            env=env, capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("administrator FIFO blocked startup instead of being refused")
    assert result.returncode == 2
    assert result.stdout == "Control site unavailable: ValueError\n"
    assert not result.stderr
    assert "synthetic-record-not-a-secret" not in result.stdout + result.stderr
    assert set(p.name for p in state.iterdir()) == {
        "admin-signing-key", "admin-password-record",
    }
    assert not (tmp_path / "absent.json").exists()


def test_private_reader_retains_regular_file_size_mode_and_symlink_boundaries(tmp_path):
    path = tmp_path / "synthetic"
    path.write_bytes(b"s" * 32)
    path.chmod(0o600)
    assert _private_bytes(path, 32) == b"s" * 32
    with pytest.raises(ValueError):
        _private_bytes(path, 31)
    path.chmod(0o640)
    with pytest.raises(ValueError):
        _private_bytes(path, 32)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        _private_bytes(link, 32)
    assert path.read_bytes() == b"s" * 32

"""A metadata FIFO must be rejected before it can retain an executor thread."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from aikey import config_store


@pytest.mark.parametrize("kind", ["fifo", "held_fifo", "symlink"])
def test_regular_metadata_reader_never_blocks_before_type_validation(tmp_path, kind):
    fifo = tmp_path / "fictional-metadata-fifo"
    os.mkfifo(fifo, 0o600)
    path, held = fifo, None
    if kind == "held_fifo":
        held = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    elif kind == "symlink":
        path = tmp_path / "fictional-metadata-link"
        path.symlink_to(fifo)
    code = '''
from pathlib import Path
import sys
from aikey.config_store import ConfigurationStore
print('entered',flush=True)
try:
    ConfigurationStore._read_regular_bytes(Path(sys.argv[1]),4096)
except OSError:
    print('refused',flush=True)
else:
    raise AssertionError('nonregular metadata accepted')
'''
    process = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(Path(config_store.__file__).resolve().parents[1])})
    try:
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == 0 and stdout == b"entered\nrefused\n" and stderr == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
        if held is not None:
            os.close(held)

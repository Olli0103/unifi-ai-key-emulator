"""An audit failure must not block the administration event loop."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes required")
@pytest.mark.parametrize("reader_present", [False, True])
def test_audit_fifo_is_counted_as_failure_without_waiting_or_writing(tmp_path, reader_present):
    path = tmp_path / "audit.jsonl"
    os.mkfifo(path, 0o600)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK) if reader_present else None
    root = Path(__file__).resolve().parents[1]
    script = (
        "import json,sys; from pathlib import Path; from aikey.admin_audit import AuditLog; "
        "log=AuditLog(Path(sys.argv[1])); result=log.record('login','invalid_credentials'); "
        "print(json.dumps({'recorded':result,'failures':log.failures}))"
    )
    try:
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, str(path)], capture_output=True, text=True,
                env=dict(os.environ, PYTHONPATH=str(root / "src")), timeout=5,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("audit FIFO blocked instead of recording an audit failure")
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout) == {"recorded": False, "failures": 1}
        assert not path.with_name(path.name + ".1").exists()
        if reader is not None:
            assert os.read(reader, 1024) == b"", "audit contents reached a nonregular file"
    finally:
        if reader is not None:
            os.close(reader)

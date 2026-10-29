"""Private/state readers must refuse a named pipe before waiting for its writer."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


CASES = [
    ("aikey.aiport_api_detection", "_read_private_key(str(path))", "private", "ApiDetectionError"),
    ("aikey.camera_inventory", "_read_private(path, 4096)", "private", "InventoryError"),
    ("aikey.aiport_candidate", "_private_file(path, 4096)", "private", "CandidateError"),
    ("aikey.database", "_read_secret(path)", "private", "DatabaseRotationError"),
    ("aikey.aiport_adoption", "AdoptionStore(path.parent, '192.0.2.1', 'a' * 64, 7443)",
     "aiport-adoption.json", "AdoptionError"),
    ("aikey.aiport_credentials", "CredentialStore(path.parent)",
     "management-credential.json", "CredentialError"),
    ("aikey.aiport_virtual_hardware", "_load_private(path, 4096, lambda value: value)",
     "private", "VirtualHardwareError"),
    ("aikey.aiport_config_store", "AiPortConfigurationStore(path)._read()",
     "private", "AiPortConfigurationError"),
    ("aikey.aiport_event_budget", "EventBudget(path.parent, limit=2)._read()",
     "aiport-event-budget.json", "EventBudgetError"),
    ("aikey.worker_archive", "_read_json(path)", "private", "ValueError"),
    ("aikey.uncertain_resolution", "_read(path)", "private", "ResolutionError"),
    ("aikey.worker", "reader._archived_record('a' * 64)", "private", "WorkerError"),
]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes required")
@pytest.mark.parametrize("module,call,filename,error", CASES, ids=[c[0] for c in CASES])
def test_private_reader_refuses_fifo_without_writer(tmp_path, module, call, filename, error):
    path = tmp_path / filename
    os.mkfifo(path, 0o600)
    marker = tmp_path / "entered"
    script = '''
import importlib, pathlib, sys
module = importlib.import_module(sys.argv[1])
path = pathlib.Path(sys.argv[2])
if sys.argv[1] == 'aikey.worker':
    reader = module.JobProcessor.__new__(module.JobProcessor)
    reader._archive_path = lambda job_id: path
else:
    reader = None
pathlib.Path(sys.argv[5]).write_text('entered')
try:
    eval(sys.argv[3], vars(module), {'path':path, 'reader':reader})
except Exception as error:
    assert type(error).__name__ == sys.argv[4], type(error).__name__
    print('refused')
else:
    raise AssertionError('nonregular_file_accepted')
'''
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    try:
        result = subprocess.run([sys.executable, "-c", script, module, str(path), call,
                                 error, str(marker)], env=env, capture_output=True,
                                text=True, timeout=5)
    except subprocess.TimeoutExpired:
        assert marker.read_text() == "entered", "reader was not reached"
        pytest.fail("nonregular private/state file blocked before type validation")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n" and not result.stderr
    assert path.is_fifo()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([filename, "entered"])

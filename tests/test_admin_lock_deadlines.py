"""An external writer cannot retain an administration worker indefinitely."""

import fcntl
import os
import time

import pytest

from aikey import aiport_config_store, aiport_rollout, config_store, file_lock


@pytest.mark.parametrize("kind", ["aikey", "aiport", "rollout"])
def test_contended_real_lock_has_deadline_and_releases_its_descriptor(tmp_path, monkeypatch, kind):
    config = tmp_path / "config.json"
    config.write_bytes(b'{"fictional":"untouched"}\n')
    if kind == "aikey":
        store = config_store.ConfigurationStore(config)
        module, lock_path, acquire, error = config_store, store.lock_path, store._locked, config_store.ConfigurationStoreError
    elif kind == "aiport":
        store = aiport_config_store.AiPortConfigurationStore(config)
        module, lock_path, acquire, error = aiport_config_store, store.lock_path, store._locked, aiport_config_store.AiPortConfigurationError
    else:
        module, lock_path, error = aiport_rollout, tmp_path / ".config.json.lock", aiport_rollout.RolloutError
        def acquire():
            return aiport_rollout._rollout_lock(config)
    monkeypatch.setattr(module, "acquire_file_lock", lambda fd: file_lock.acquire(fd, timeout=.15))
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with pytest.raises(error):
            with acquire():
                pytest.fail("An external writer's lock was ignored")
        assert .1 <= time.monotonic() - started < 1
        assert config.read_bytes() == b'{"fictional":"untouched"}\n'
    finally:
        os.close(descriptor)
    # The failed attempt has closed its own descriptor; a later attempt works.
    with acquire():
        pass


def test_unrelated_lock_error_is_not_retried(monkeypatch):
    import errno
    calls = []
    def refused(*args):
        calls.append(args)
        raise OSError(errno.EBADF, "fictional descriptor failure")
    monkeypatch.setattr(file_lock.fcntl, "flock", refused)
    with pytest.raises(OSError) as result:
        file_lock.acquire(-1)
    assert result.value.errno == errno.EBADF and len(calls) == 1

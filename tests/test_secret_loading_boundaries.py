"""Unsafe local credential inputs fail before device/provider work can begin."""

import asyncio
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest

from aikey.config import ConfigError, hydrate_secrets
from aikey.search import EmbeddingError, EmbeddingService
from aikey.secret_files import SecretFileError, read_secret


CANARY = "synthetic-credential-file-canary"


def private(path, value):
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def configuration(tmp_path, role):
    management = private(tmp_path / "management", b"synthetic-management-password\n")
    target = management if role == "management" else private(tmp_path / role, CANARY.encode())
    config = {"device": {"management_password_file": str(management)}, "inference": {}}
    if role == "vision":
        config["inference"]["api_key_file"] = str(target)
    elif role == "speech":
        config["speech_to_text"] = {"api_key_file": str(target)}
    return config, target


class NoContact:
    closed = False

    def post(self, *args, **kwargs):
        raise AssertionError("provider_contact_before_secret_validation")


async def embedding_attempt(path):
    service = EmbeddingService({"backend": "http", "base_url": "http://127.0.0.1:1",
                                "bearer_token_file": str(path)})
    service._session = NoContact()
    await service._http_encode(["synthetic input"])


@pytest.mark.parametrize("role", ["management", "vision", "speech", "embedding"])
@pytest.mark.parametrize("invalid", ["symlink", "permissions", "oversize", "invalid_utf8", "empty"])
def test_unsafe_credential_inputs_refuse_without_leaking_or_contact(tmp_path, role, invalid):
    config, target = configuration(tmp_path, role)
    original = copy.deepcopy(config)
    if invalid == "symlink":
        other = private(tmp_path / "original", CANARY.encode())
        target.unlink()
        target.symlink_to(other)
    elif invalid == "permissions":
        target.chmod(0o640)
    elif invalid == "oversize":
        target.write_bytes(CANARY.encode() + b"x" * 4097)
    elif invalid == "invalid_utf8":
        target.write_bytes(b"\xff" + CANARY.encode())
    else:
        target.write_bytes(b" \n")
    before = target.read_bytes()
    expected = EmbeddingError if role == "embedding" else ConfigError
    with pytest.raises(expected) as error:
        if role == "embedding":
            asyncio.run(embedding_attempt(target))
        else:
            hydrate_secrets(config)
    assert CANARY not in str(error.value) and str(target) not in str(error.value)
    assert config == original and target.read_bytes() == before
    assert target.is_symlink() == (invalid == "symlink")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes required")
@pytest.mark.parametrize("role", ["management", "vision", "speech", "embedding"])
def test_credential_fifo_cannot_block_startup_or_embedding_event_loop(tmp_path, role):
    config, target = configuration(tmp_path, role)
    target.unlink()
    os.mkfifo(target, 0o600)
    # Inputs are wholly synthetic. The child can only reach the fake session.
    script = '''
import asyncio, json, pathlib, sys
from aikey.config import hydrate_secrets, ConfigError
from aikey.search import EmbeddingError
from test_secret_loading_boundaries import embedding_attempt
config = json.loads(sys.argv[1])
pathlib.Path(sys.argv[3]).write_text('entered')
try:
    if sys.argv[4] == 'embedding':
        asyncio.run(embedding_attempt(pathlib.Path(sys.argv[2])))
    else:
        hydrate_secrets(config)
except (ConfigError, EmbeddingError):
    print('refused')
else:
    raise AssertionError('credential_fifo_accepted')
'''
    import json
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "tests")]))
    marker = tmp_path / "entered"
    try:
        result = subprocess.run([sys.executable, "-c", script, json.dumps(config), str(target),
                                 str(marker), role], env=env, capture_output=True, text=True,
                                timeout=5)
    except subprocess.TimeoutExpired:
        assert marker.read_text() == "entered"
        pytest.fail("credential FIFO blocked before type validation")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused\n" and not result.stderr and target.is_fifo()


@pytest.mark.parametrize("role", ["management", "vision", "speech"])
def test_private_regular_credentials_still_load_into_copy_only(tmp_path, role):
    config, target = configuration(tmp_path, role)
    before = copy.deepcopy(config)
    result = hydrate_secrets(config)
    assert config == before
    assert result["device"]["management_password"] == "synthetic-management-password"
    if role in {"vision", "speech"}:
        assert result["inference" if role == "vision" else "speech_to_text"]["api_key"] == CANARY
    assert target.stat().st_mode & 0o077 == 0


def test_secret_limit_is_in_bytes_and_growth_after_stat_is_still_refused(tmp_path, monkeypatch):
    path = private(tmp_path / "boundary", ("é" * 2048).encode())
    assert read_secret(path) == "é" * 2048
    path.write_bytes(b"short-synthetic-value")
    original_fstat = os.fstat
    opened = []

    def grow_after_check(descriptor):
        info = original_fstat(descriptor)
        opened.append(descriptor)
        path.write_bytes(b"x" * 5000)
        return info

    monkeypatch.setattr(os, "fstat", grow_after_check)
    with pytest.raises(SecretFileError, match="^credential_file_unavailable$"):
        read_secret(path)
    assert len(opened) == 1
    with pytest.raises(OSError):
        original_fstat(opened[0])


def test_secret_directory_refuses_without_exposing_path(tmp_path):
    with pytest.raises(SecretFileError, match="^credential_file_unavailable$"):
        read_secret(tmp_path)

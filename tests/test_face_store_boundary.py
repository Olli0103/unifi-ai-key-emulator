"""Private named-template storage uses fictional vectors/names only."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aikey.faces import FaceStore, FaceStoreError
from aikey import faces

TEMPLATE = [1.0] + [0.0] * 127


def record():
    return {"schema": 1, "identities": {"Synthetic test identity": {
        "templates": [list(TEMPLATE)], "created": 1}}}


def write_store(root, data):
    store = FaceStore(root)
    store.directory.mkdir(mode=0o700)
    store.path.write_text(data if isinstance(data, str) else json.dumps(data))
    store.path.chmod(0o600)
    return store


@pytest.mark.parametrize("variant", ["short", "long", "zero", "scaled", "boolean", "nonfinite",
    "huge_integer", "duplicate", "bad_root", "bad_schema", "boolean_schema", "extra_root",
    "bad_identities", "bad_name", "blank_name", "untrimmed_name", "bad_entry", "extra_entry",
    "bad_created", "boolean_created", "bad_templates", "empty_templates", "too_many_templates",
    "too_many_identities"])
def test_invalid_stored_templates_are_refused_without_repair_or_disclosure(tmp_path, variant, capsys):
    data = record()
    entry = data["identities"]["Synthetic test identity"]
    if variant == "short":
        entry["templates"][0] = [1.0]
    elif variant == "long":
        entry["templates"][0].append(0.0)
    elif variant == "zero":
        entry["templates"][0] = [0.0] * 128
    elif variant == "scaled":
        entry["templates"][0][0] = 2.0
    elif variant == "boolean":
        entry["templates"][0][0] = True
    elif variant == "nonfinite":
        entry["templates"][0][0] = float("nan")
    elif variant == "huge_integer":
        entry["templates"][0][0] = 10 ** 400
    elif variant == "duplicate":
        data = '{"schema":1,"identities":{},"identities":{"PRIVATE-CANARY":{"templates":[[1]],"created":1}}}'
    elif variant == "bad_root":
        data = []
    elif variant == "bad_schema":
        data["schema"] = 2
    elif variant == "boolean_schema":
        data["schema"] = True
    elif variant == "extra_root":
        data["PRIVATE-CANARY"] = "not a supported field"
    elif variant == "bad_identities":
        data["identities"] = []
    elif variant in {"bad_name", "blank_name", "untrimmed_name"}:
        name = {"bad_name": "PRIVATE-CANARY\n", "blank_name": " ", "untrimmed_name": " padded "}[variant]
        data["identities"] = {name: entry}
    elif variant == "bad_entry":
        data["identities"] = {"Synthetic test identity": []}
    elif variant == "extra_entry":
        entry["PRIVATE-CANARY"] = True
    elif variant == "bad_created":
        entry["created"] = "PRIVATE-CANARY"
    elif variant == "boolean_created":
        entry["created"] = True
    elif variant == "bad_templates":
        entry["templates"] = {}
    elif variant == "empty_templates":
        entry["templates"] = []
    elif variant == "too_many_templates":
        entry["templates"] *= 21
    elif variant == "too_many_identities":
        data["identities"] = {f"Synthetic {i}": copy.deepcopy(entry) for i in range(51)}
    store = write_store(tmp_path, data)
    before = store.path.read_bytes()
    with pytest.raises(FaceStoreError) as caught:
        store.match(TEMPLATE)
    assert str(caught.value) in {"face_store_invalid", "face_store_not_private", "face_store_unavailable"}
    assert "PRIVATE-CANARY" not in str(caught.value)
    assert store.path.read_bytes() == before
    assert capsys.readouterr().out == ""


def test_correct_private_records_still_match_delete_and_keep_device_state(tmp_path):
    state = tmp_path / "device-state.json"
    state.write_bytes(b'{"fictional-identity":"retained"}')
    store = write_store(tmp_path, record())
    assert store.names() == ["Synthetic test identity"]
    assert store.match(TEMPLATE)[0] == "Synthetic test identity"
    assert store.delete("Synthetic test identity")
    assert store.names() == [] and store.match(TEMPLATE) == (None, [])
    assert state.read_bytes() == b'{"fictional-identity":"retained"}'


def test_symlink_and_permissive_store_refused_unchanged(tmp_path):
    store = write_store(tmp_path, record())
    original = store.path.read_bytes()
    store.path.chmod(0o644)
    with pytest.raises(FaceStoreError):
        store.names()
    assert store.path.read_bytes() == original and store.path.stat().st_mode & 0o777 == 0o644
    store.path.chmod(0o600)
    target = store.path.with_name("synthetic-original.json")
    store.path.rename(target)
    store.path.symlink_to(target)
    with pytest.raises(FaceStoreError):
        store.names()
    assert store.path.is_symlink() and target.read_bytes() == original


def test_fifo_store_is_refused_without_blocking(tmp_path):
    store = FaceStore(tmp_path)
    store.directory.mkdir(mode=0o700)
    os.mkfifo(store.path, 0o600)
    code = """
from pathlib import Path
import sys
from aikey.faces import FaceStore, FaceStoreError
try:
    FaceStore(Path(sys.argv[1])).names()
except FaceStoreError:
    raise SystemExit(0)
raise SystemExit(3)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    try:
        child = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env,
                               capture_output=True, timeout=2, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("Private named-template FIFO blocked before type validation")
    assert child.returncode == 0 and child.stdout == b"" and child.stderr == b""


def test_missing_store_is_empty_without_creating_anything(tmp_path):
    store = FaceStore(tmp_path)
    assert store.names() == [] and store.match(TEMPLATE) == (None, [])
    assert list(tmp_path.iterdir()) == []


def test_foreign_owner_is_refused_on_the_open_descriptor(tmp_path, monkeypatch):
    store = write_store(tmp_path, record())
    original = store.path.read_bytes()
    actual = os.fstat
    def foreign_owner(fd):
        info = actual(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_uid=os.geteuid() + 1)
    monkeypatch.setattr(faces.os, "fstat", foreign_owner)
    with pytest.raises(FaceStoreError, match="^face_store_not_private$"):
        store.names()
    assert store.path.read_bytes() == original


def test_oversize_store_is_refused_before_parsing(tmp_path, monkeypatch):
    store = write_store(tmp_path, record())
    original = store.path.read_bytes()
    monkeypatch.setattr(faces, "_MAX_STORE_BYTES", 16)
    with pytest.raises(FaceStoreError, match="^face_store_invalid$"):
        store.names()
    assert store.path.read_bytes() == original


def test_growth_after_fstat_is_bounded_and_not_repaired(tmp_path, monkeypatch):
    store = write_store(tmp_path, record())
    original = store.path.read_bytes()
    monkeypatch.setattr(faces, "_MAX_STORE_BYTES", len(original) + 2)
    actual = os.fstat
    def grow_after_stat(fd):
        info = actual(fd)
        with store.path.open("ab") as destination:
            destination.write(b" " * 32)
        return info
    monkeypatch.setattr(faces.os, "fstat", grow_after_stat)
    with pytest.raises(FaceStoreError, match="^face_store_invalid$"):
        store.names()
    assert store.path.read_bytes() == original + b" " * 32


@pytest.mark.parametrize("raw", ["", b"\xff\xfe"])
def test_empty_or_non_utf8_state_has_a_fixed_cli_error(tmp_path, capsys, raw):
    store = FaceStore(tmp_path)
    store.directory.mkdir(mode=0o700)
    store.path.write_bytes(raw.encode() if isinstance(raw, str) else raw)
    store.path.chmod(0o600)
    assert faces.main(["list", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "face_store_invalid" in output.err
    assert str(tmp_path) not in output.err

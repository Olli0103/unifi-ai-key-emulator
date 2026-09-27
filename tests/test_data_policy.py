"""The data-flow and retention table must match the code (#5).

Each test fails if a row's expiry claim goes stale (its mechanism is removed
or changed) or is overstated (something claims to expire that does not).
All state is synthetic.
"""

import ast
import importlib
import inspect
import json
from pathlib import Path
import time

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest

from aikey import data_policy
from aikey.admin_security import AdminSecurity
from aikey.control_site import ControlSite
from aikey.data_policy import EXPIRY_KINDS, ROWS, live_status
from aikey.faces import FaceStore
from aikey.worker import AUDIO_TEMP_PREFIX
from aikey.worker_archive import plan, inventory
from test_control_site import fixture

SRC = Path(data_policy.__file__).parent
ROW = {row["key"]: row for row in ROWS}


def _resolve(reference):
    module, _, attribute = reference.partition(":")
    value = importlib.import_module(module)
    for part in attribute.split("."):
        value = getattr(value, part)
    return value


def _calls(name):
    """Modules under src/aikey that call ``name(`` (by attribute or bare name)."""
    found = set()
    for path in SRC.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                target = node.func
                called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
                if called == name:
                    found.add(path.stem)
    return found


def test_every_row_is_complete_and_no_row_claims_automatic_expiry():
    assert set(ROW) == {"search_backups", "worker_archive", "face_store", "transcripts"}
    for row in ROWS:
        assert row["expiry"] in EXPIRY_KINDS                   # "automatic" is not a kind
        assert row["category"] and row["local"] and row["detail"] and row["owner_decision"]
        assert row["evidence"], row["key"]
        for reference in row["evidence"]:
            assert _resolve(reference) is not None, reference   # the mechanism still exists


def test_search_backup_expiry_is_manual_and_dry_by_default():
    prune = _resolve("aikey.search_backup:prune")
    assert inspect.signature(prune).parameters["apply"].default is False
    # "Manual only": nothing in the package schedules prune besides its own CLI.
    assert _calls("prune") == {"search_backup"}


def test_worker_archive_claims_no_expiry_and_has_no_delete_path(tmp_path):
    source = (SRC / "worker_archive.py").read_text()
    tree = ast.parse(source)
    deleting = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"unlink", "rmtree", "remove", "rmdir"}}
    assert deleting == set()
    assert ROW["worker_archive"]["expiry"] == "none"
    # Without controller evidence the dry-run plan expires nothing.
    job = "a" * 64
    bucket = tmp_path / "worker-archive" / job[:2]
    bucket.mkdir(parents=True, mode=0o700)
    (tmp_path / "worker-archive").chmod(0o700)
    (bucket / f"{job}.json").write_text(json.dumps({
        "schema": 1, "jobId": job, "fingerprint": "b" * 64, "state": "completed",
        "updatedAt": time.time() - 400 * 86400}))
    assert plan(inventory(tmp_path), min_age_days=1)["expirable"] == []


def test_face_identities_never_expire_by_time_and_only_delete_or_purge_removes_them(tmp_path, monkeypatch):
    store = FaceStore(tmp_path)
    embedding = [1.0] + [0.0] * 127
    store.enroll("Synthetic Person", embedding)
    monkeypatch.setattr(time, "time", lambda: 4_000_000_000.0)   # far in the future
    assert FaceStore(tmp_path).match(embedding)[0] == "Synthetic Person"
    assert store.delete("Synthetic Person") is True and store.names() == []
    store.enroll("Other Synthetic", embedding)
    store.purge()
    assert store.names() == [] and ROW["face_store"]["expiry"] == "none"


def test_transcripts_are_not_kept_locally_and_audio_is_per_job():
    assert ROW["transcripts"]["expiry"] == "not_stored"
    execute = inspect.getsource(_resolve("aikey.worker:JobProcessor._execute_speech"))
    assert 'result["result"] = {"segments": len(segments)}' in execute
    audio = inspect.getsource(_resolve("aikey.worker:JobProcessor._audio"))
    assert "tempfile.TemporaryDirectory(prefix=AUDIO_TEMP_PREFIX" in audio
    # The behavioural proof lives with the speech tests; keep it from being dropped.
    speech_tests = (SRC.parent.parent / "tests" / "test_speech_to_text.py").read_text()
    assert '"Hello" not in journal' in speech_tests


def test_a_false_claim_is_caught(monkeypatch):
    """A row claiming a mechanism that does not exist fails the completeness check."""
    broken = dict(ROW["face_store"], evidence=("aikey.faces:FaceStore.expire_old",))
    monkeypatch.setattr(data_policy, "ROWS", (broken,))
    with pytest.raises(AttributeError):
        for reference in data_policy.ROWS[0]["evidence"]:
            _resolve(reference)


def test_live_status_is_counts_only(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    FaceStore(root).enroll("Private Name", [1.0] + [0.0] * 127)
    (root / "worker-jobs").mkdir(mode=0o700)
    (root / "worker-jobs" / f"{AUDIO_TEMP_PREFIX}crash1").mkdir()
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "search-20260901T010000.json").write_text("{}")
    status = live_status(root, backups)
    assert status == {"search_backups": {"backups": 1},
                      "worker_archive": {"markers": 0, "problems": 0},
                      "face_store": {"identities": 1},
                      "transcripts": {"audio_leftovers": 1}}
    assert "Private Name" not in json.dumps(status)
    assert live_status(None) == dict.fromkeys(status)


async def test_the_page_shows_categories_and_counts_never_values_or_paths(tmp_path):
    key_config, port_config = fixture(tmp_path)
    root = tmp_path / "aikey-state"
    root.mkdir(mode=0o700)
    FaceStore(root).enroll("Private Name", [1.0] + [0.0] * 127)
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"s" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765,
                       aikey_state_dir=root)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        assert (await client.get("/data-retention", allow_redirects=False)).status == 303
        await client.post("/login", data={"password": password}, headers={"Origin": site.origin},
                          allow_redirects=False)
        assert "/data-retention" in await (await client.get("/")).text()
        page = await (await client.get("/data-retention")).text()
        for row in ROWS:
            assert row["category"].split(" (")[0] in page
        assert "identities: 1" in page and "Manual only (not scheduled)" in page
        assert page.count("needs_evidence") == 4
        assert "Private Name" not in page and str(tmp_path) not in page
        assert "<form" not in page.split("Data flow and retention</h1>")[1]
        assert (await client.post("/data-retention", headers={"Origin": site.origin})).status == 405
    finally:
        await client.close()
        await server.close()

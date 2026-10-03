"""Read-only backup/restore preflight for AI Key and AI Port state (#13). Synthetic only."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from aikey.state_backup_preflight import MANIFEST_SCHEMA, inventory, validate

KEY_MAC, SLOT2_MAC, SLOT3_MAC = "02:00:00:00:00:A1", "2A:11:00:00:00:02", "2A:11:00:00:00:03"


def write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    path.chmod(mode)


def make_key(root):
    write(root / "config.json", {"runtime": {"state_dir": "/state"},
                                 "controller": {"ca_file": "/state/controller-ca.pem"},
                                 "inference": {"api_key_file": "/state/provider-key-vision"},
                                 "device": {"mac": KEY_MAC}})
    write(root / "device-state.json", {"schema": 1, "mac": KEY_MAC, "adopted": True})
    for name in ("device.crt", "device.key", "controller-ca.pem", "provider-key-vision"):
        write(root / name, f"synthetic {name}\n")
    return root


def make_slot(root, mac):
    write(root / "config.json", {"mac": mac, "paired_streams": [],
                                 "live_pool_detector": {"provider_config": {
                                     "api_key_file": "/state/provider-key-openai"}}})
    for name in ("identity.json", "device.crt", "device.key", "controller-ca.pem", "provider-key-openai"):
        write(root / name, f"synthetic {name}\n")
    return root


@pytest.fixture
def live(tmp_path):
    return {"aikey": ("aikey", make_key(tmp_path / "live" / "key")),
            "aiport:nas-2": ("aiport", make_slot(tmp_path / "live" / "slot-2", SLOT2_MAC)),
            "aiport:nas-3": ("aiport", make_slot(tmp_path / "live" / "slot-3", SLOT3_MAC))}


def backup_of(tmp_path, live, *, name="backup", source=None):
    """Copy every file of each live profile into a candidate backup with a manifest."""
    root = tmp_path / name
    manifest = {"schema": MANIFEST_SCHEMA, "profiles": {}}
    for label, (_, directory) in live.items():
        target = root / label.replace(":", "_")
        origin = (source or {}).get(label, directory)
        shutil.copytree(origin, target)
        manifest["profiles"][label] = {"files": {
            p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(target.rglob("*")) if p.is_file()}}
    write(root / "manifest.json", manifest)
    return root


def digest(root):
    value = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        value.update(str(path.relative_to(root)).encode() + path.read_bytes())
    return value.hexdigest()


def codes(report, label):
    return report["profiles"][label]["problems"]


def test_a_complete_state_is_ready_and_reported_without_identifiers(live):
    report = inventory(live)
    assert report["ready_for_backup"] is True
    assert report["profiles"]["aikey"] == {
        "kind": "aikey", "readable": True, "required": 5, "required_present": 5,
        "secret_references": 1, "secrets_present": 1, "journals_present": 0,
        "sensitive_present": 0, "problems": []}
    text = json.dumps(report)
    for secret in (KEY_MAC, SLOT2_MAC, "provider-key", "device.key", str(live["aikey"][1])):
        assert secret not in text


@pytest.mark.parametrize("damage,label,code", [
    (lambda key, slot: (key / "device.key").unlink(), "aikey", "missing:tls_key"),
    (lambda key, slot: (key / "device-state.json").unlink(), "aikey", "missing:device_identity"),
    (lambda key, slot: (slot / "provider-key-openai").unlink(), "aiport:nas-2", "secret_missing:provider_key"),
    (lambda key, slot: ((slot / "device.key").unlink(), (slot / "device.key").symlink_to(key / "device.key")),
     "aiport:nas-2", "symlink:tls_key"),
    (lambda key, slot: (slot / "device.key").chmod(0o644), "aiport:nas-2", "unsafe_mode:tls_key"),
    (lambda key, slot: (slot / "config.json").write_text("{broken"), "aiport:nas-2", "config_unreadable"),
])
def test_an_incomplete_live_state_is_not_ready(live, damage, label, code):
    damage(live["aikey"][1], live["aiport:nas-2"][1])
    report = inventory(live)
    assert report["ready_for_backup"] is False and code in codes(report, label)


def test_two_profiles_sharing_one_identity_are_flagged(live):
    write(live["aiport:nas-3"][1] / "config.json", {"mac": SLOT2_MAC, "paired_streams": [],
                                                    "live_pool_detector": {"provider_config": {
                                                        "api_key_file": "/state/provider-key-openai"}}})
    report = inventory(live)
    assert "identity_shared_with_another_profile" in codes(report, "aiport:nas-2")
    assert "identity_shared_with_another_profile" in codes(report, "aiport:nas-3")


def test_a_faithful_backup_is_restorable_and_nothing_is_written(tmp_path, live):
    backup = backup_of(tmp_path, live)
    before = digest(tmp_path)
    result = validate(backup, live)
    assert result == {"restorable": True, "problems": [], "profiles": {
        label: {"files": 6, "problems": []} for label in live}}
    assert digest(tmp_path) == before                               # read-only
    assert KEY_MAC not in json.dumps(result) and "provider-key" not in json.dumps(result)


def test_a_backup_of_another_device_identity_is_refused(tmp_path, live):
    other = make_key(tmp_path / "other-key")
    write(other / "device-state.json", {"schema": 1, "mac": "02:00:00:00:00:FF", "adopted": True})
    result = validate(backup_of(tmp_path, live, source={"aikey": other}), live)
    assert result["restorable"] is False and "identity_mismatch" in codes(result, "aikey")


def test_a_stale_or_unknown_revision_is_reported(tmp_path, live):
    slot = live["aiport:nas-2"][1]
    old = (slot / "config.json").read_bytes()
    history = slot / ".aiport-config-history"
    history.mkdir(mode=0o700)
    (history / f"{hashlib.sha256(old).hexdigest()}.json").write_bytes(old)
    backup = backup_of(tmp_path, live)                              # captures the old revision
    write(slot / "config.json", {"mac": SLOT2_MAC, "paired_streams": [{"x": 1}],
                                 "live_pool_detector": {"provider_config": {
                                     "api_key_file": "/state/provider-key-openai"}}})
    assert "stale_revision" in codes(validate(backup, live), "aiport:nas-2")
    (history / f"{hashlib.sha256(old).hexdigest()}.json").unlink()
    assert "revision_not_in_history" in codes(validate(backup, live), "aiport:nas-2")


def test_a_missing_secret_blocks_the_restore(tmp_path, live):
    backup = backup_of(tmp_path, live)
    target = backup / "aiport_nas-3"
    (target / "provider-key-openai").unlink()
    manifest = json.loads((backup / "manifest.json").read_text())
    del manifest["profiles"]["aiport:nas-3"]["files"]["provider-key-openai"]
    (backup / "manifest.json").write_text(json.dumps(manifest))
    result = validate(backup, live)
    assert result["restorable"] is False and "missing_secret:provider_key" in codes(result, "aiport:nas-3")


@pytest.mark.parametrize("damage,code", [
    (lambda b: (b / "aikey" / "device.crt").unlink(), "partial_archive"),
    (lambda b: (b / "aikey" / "device.crt").write_text("tampered"), "digest_mismatch"),
    (lambda b: (b / "manifest.json").write_text("{broken"), None),
])
def test_a_partial_or_tampered_archive_is_refused(tmp_path, live, damage, code):
    backup = backup_of(tmp_path, live)
    damage(backup)
    result = validate(backup, live)
    assert result["restorable"] is False
    if code:
        assert code in codes(result, "aikey")
    else:
        assert result["problems"] == ["manifest_invalid"]


def test_one_slots_files_in_another_slots_backup_are_contamination(tmp_path, live):
    swapped = {"aiport:nas-2": live["aiport:nas-3"][1]}              # slot-3's state filed as slot-2
    result = validate(backup_of(tmp_path, live, source=swapped), live)
    assert result["restorable"] is False
    assert "cross_slot_contamination" in codes(result, "aiport:nas-2")
    assert "identity_duplicated_across_profiles" in result["problems"]


def test_a_backup_missing_a_profile_or_naming_an_unknown_one_is_refused(tmp_path, live):
    partial = backup_of(tmp_path, {k: v for k, v in live.items() if k != "aiport:nas-3"})
    assert validate(partial, live)["problems"] == ["profiles_missing:1"]
    manifest = json.loads((partial / "manifest.json").read_text())
    manifest["profiles"]["aiport:nas-9"] = {"files": {}}
    (partial / "manifest.json").write_text(json.dumps(manifest))
    assert "unknown_profile" in codes(validate(partial, live), "aiport:nas-9")


# --- control-site view ------------------------------------------------------

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from test_control_site import fixture  # noqa: E402


async def test_the_control_site_shows_counts_and_codes_only_behind_sign_in(tmp_path):
    key_config, port_config = fixture(tmp_path)
    password = "synthetic-admin-passphrase"
    site = ControlSite(key_config, port_config, signing_key=b"k" * 32,
                       password_record=AdminSecurity.create_password_record(password), port=8765)
    server = TestServer(site.app(), host="127.0.0.1")
    await server.start_server()
    site.origin = f"http://127.0.0.1:{server.port}"
    site.security = AdminSecurity(b"k" * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        assert (await client.get("/state-backup-preflight", allow_redirects=False)).status == 303
        await client.post("/login", data={"password": password}, headers={"Origin": site.origin},
                          allow_redirects=False)
        before = digest(tmp_path)
        page = await (await client.get("/state-backup-preflight")).text()
        assert "Backup and restore preflight" in page and "<td>aikey</td>" in page and "<td>aiport</td>" in page
        # The synthetic Key state has no device identity or TLS key yet: reported by code.
        assert "<code>missing:device_identity</code>" in page and "Ready for backup: no" in page
        for secret in (str(tmp_path), "2A1100F0A55E", "device.key", "provider-key"):
            assert secret not in page
        assert "<form" not in page.split("</h1>")[1]
        assert (await client.post("/state-backup-preflight", headers={"Origin": site.origin})).status == 405
        assert digest(tmp_path) == before
    finally:
        await client.close()
        await server.close()

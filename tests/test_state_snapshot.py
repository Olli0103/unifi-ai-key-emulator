"""Cold full-file backup and fresh offline restore, with byte/path race cases."""

import hashlib
import json
import os
import ssl
import tarfile
import time

import pytest

from aikey import state_snapshot as snapshot
from aikey.config import atomic_private, initialize
from aikey.tls import ensure_identity_certificate


def source(tmp_path, kind):
    root = tmp_path / kind
    if kind == "aikey":
        initialize(root / "config.json", root, controller_host="127.0.0.1")
    else:
        root.mkdir(mode=0o700)
        ensure_identity_certificate(root, "02000000F004")
        binding = {"controller_ip": "192.168.40.1", "controller_pin": hashlib.sha256(
            ssl.PEM_cert_to_DER_cert((root / "device.crt").read_text())).hexdigest(), "control_port": 7442}
        atomic_private(root / "config.json", json.dumps({"mac": "02000000F004", **binding,
            "live_pool_detector": {"provider_config": {"api_key_file": "/state/private/provider-key"}}}))
        atomic_private(root / "aiport-adoption.json", json.dumps({**binding, "phase": "adopted"}))
        (root / "private").mkdir(mode=0o700)
        atomic_private(root / "private/provider-key", b"fictional-never-issued-key")
    atomic_private(root / "controller-ca.pem", (root / "device.crt").read_bytes())
    (root / "empty-journal").mkdir(mode=0o700)
    (root / "faces").mkdir(mode=0o700)
    atomic_private(root / "faces/identities.json", b'{"fictional":"synthetic-enrollment-copy-only"}')
    return root


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def saved(tmp_path, kind):
    root = source(tmp_path, kind)
    output = tmp_path / (kind + ".tar")
    revision = hashlib.sha256((root / "config.json").read_bytes()).hexdigest()
    report = snapshot.backup(root, output, kind=kind, expected_revision=revision)
    return root, output, report


@pytest.mark.parametrize("kind", ["aikey", "aiport"])
def test_full_profile_files_secrets_journals_identity_and_empty_dirs_restore_exactly(tmp_path, kind):
    root, archive, report = saved(tmp_path, kind)
    original = contents(root)
    destination = tmp_path / "restored"
    result = snapshot.restore(archive, destination, expected_sha256=report["sha256"],
        expected_identity=report["identity"], expected_revision=report["revision"])
    assert result["restored"] is True and result["activated"] is False
    assert contents(destination) == contents(root) == original
    assert (destination / "empty-journal").is_dir()
    assert archive.stat().st_mode & 0o077 == 0
    assert all(p.stat().st_mode & 0o077 == 0 for p in destination.rglob("*"))
    assert report["files"] == len(original)


@pytest.mark.parametrize("field", ["sha256", "identity", "revision"])
def test_wrong_archive_identity_revision_or_digest_refuses_before_destination(tmp_path, field):
    root, archive, report = saved(tmp_path, "aikey")
    report[field] = "0" * 64
    destination = tmp_path / "must-not-exist"
    with pytest.raises(snapshot.SnapshotError):
        snapshot.restore(archive, destination, expected_sha256=report["sha256"],
            expected_identity=report["identity"], expected_revision=report["revision"])
    assert not destination.exists()


def test_existing_live_destination_is_never_overwritten(tmp_path):
    root, archive, report = saved(tmp_path, "aiport")
    before = contents(root)
    with pytest.raises(snapshot.SnapshotError, match="snapshot_destination_exists"):
        snapshot.restore(archive, root, expected_sha256=report["sha256"],
            expected_identity=report["identity"], expected_revision=report["revision"])
    assert contents(root) == before


def test_external_reference_is_not_followed_and_never_published_as_complete_backup(tmp_path):
    root = source(tmp_path, "aiport")
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"fictional-outside-value-not-to-export")
    config = json.loads((root / "config.json").read_text())
    config["live_pool_detector"]["provider_config"]["api_key_file"] = str(outside)
    atomic_private(root / "config.json", json.dumps(config))
    output = tmp_path / "must-not-exist.tar"
    with pytest.raises(snapshot.SnapshotError, match="external_snapshot_reference_not_supported"):
        snapshot.backup(root, output, kind="aiport",
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert not output.exists() and outside.read_bytes() == b"fictional-outside-value-not-to-export"
    assert not list(tmp_path.glob(".snapshot-*"))


@pytest.mark.parametrize("special", ["fifo", "symlink", "hardlink"])
def test_special_or_aliased_source_refused_without_blocking_or_following(tmp_path, special):
    root = source(tmp_path, "aikey")
    path = root / "special"
    if special == "fifo":
        os.mkfifo(path, 0o600)
    elif special == "symlink":
        path.symlink_to(root / "device.key")
    else:
        os.link(root / "device.key", path)
    started = time.monotonic()
    with pytest.raises((snapshot.SnapshotError, OSError)):
        snapshot.backup(root, tmp_path / "rejected.tar", kind="aikey",
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert time.monotonic() - started < 1 and not (tmp_path / "rejected.tar").exists()


def test_torn_actual_tar_copy_is_rejected_even_if_source_bytes_are_restored_before_postcheck(tmp_path, monkeypatch):
    root = source(tmp_path, "aikey")
    target = root / "large-journal"
    original = b"a" * 65536
    atomic_private(target, original)
    real = tarfile.TarFile.addfile
    changed = []
    def interposed(archive, member, content=None):
        if member.name == "large-journal":
            read, count = content.read, 0
            def torn(size):
                nonlocal count
                raw = read(size)
                count += len(raw)
                if not changed:
                    with target.open("r+b") as output:
                        output.write(b"b" * len(original))
                    changed.append(True)
                if count == len(original):
                    with target.open("r+b") as output:
                        output.write(original)
                return raw
            content.read = torn
        return real(archive, member, content)
    monkeypatch.setattr(tarfile.TarFile, "addfile", interposed)
    with pytest.raises(snapshot.SnapshotError, match="snapshot_input_changed"):
        snapshot.backup(root, tmp_path / "torn.tar", kind="aikey",
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert changed and target.read_bytes() == original and not (tmp_path / "torn.tar").exists()


def test_archive_path_replacement_cannot_change_the_verified_restored_bytes(tmp_path, monkeypatch):
    root, archive, report = saved(tmp_path, "aikey")
    real, changed = snapshot.inspect_archive, []
    def replace_after_verification(source, **kwargs):
        result = real(source, **kwargs)
        archive.write_bytes(b"fictional-replaced-archive-path-canary")
        changed.append(True)
        return result
    monkeypatch.setattr(snapshot, "inspect_archive", replace_after_verification)
    destination = tmp_path / "restored"
    snapshot.restore(archive, destination, expected_sha256=report["sha256"],
        expected_identity=report["identity"], expected_revision=report["revision"])
    assert changed and contents(destination) == contents(root)
    assert archive.read_bytes() == b"fictional-replaced-archive-path-canary"


def test_output_parent_retarget_cannot_publish_into_replacement_canary(tmp_path, monkeypatch):
    root = source(tmp_path, "aikey")
    parent = tmp_path / "backups"
    parent.mkdir(mode=0o700)
    original, changed = snapshot.inspect_archive, []
    def replace_parent(stream, **kwargs):
        result = original(stream, **kwargs)
        parent.rename(tmp_path / "retained-output-parent")
        parent.mkdir(mode=0o700)
        (parent / "canary").write_bytes(b"untouched")
        changed.append(True)
        return result
    monkeypatch.setattr(snapshot, "inspect_archive", replace_parent)
    with pytest.raises(snapshot.SnapshotError, match="snapshot_destination_path_changed"):
        snapshot.backup(root, parent / "profile.tar", kind="aikey",
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert changed and [p.name for p in parent.iterdir()] == ["canary"]
    assert (parent / "canary").read_bytes() == b"untouched"


@pytest.mark.parametrize("kind", [tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.SYMTYPE])
def test_extension_body_and_link_rejected_before_reader_or_restore_allocation(tmp_path, kind):
    root, archive, report = saved(tmp_path, "aikey")
    member = tarfile.TarInfo("extension")
    member.type = kind
    member.size = snapshot.MAX_BYTES + 1 if kind != tarfile.SYMTYPE else 0
    member.linkname = "../outside-canary"
    archive.write_bytes(member.tobuf(format=tarfile.USTAR_FORMAT))
    destination = tmp_path / "must-not-exist"
    with pytest.raises(snapshot.SnapshotError, match="snapshot_extension_or_link_refused"):
        snapshot.restore(archive, destination,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            expected_identity=report["identity"], expected_revision=report["revision"])
    assert not destination.exists()


def test_payload_appended_after_tar_end_is_not_silently_ignored(tmp_path):
    root, archive, report = saved(tmp_path, "aikey")
    with archive.open("ab") as output:
        output.write(b"nonzero-hidden-trailing-fixture")
    destination = tmp_path / "must-not-exist"
    with pytest.raises(snapshot.SnapshotError, match="snapshot_trailing_payload"):
        snapshot.restore(archive, destination,
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            expected_identity=report["identity"], expected_revision=report["revision"])
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["aikey", "aiport"])
def test_cross_slot_identity_or_controller_adoption_mix_is_not_a_valid_backup(tmp_path, kind):
    root = source(tmp_path, kind)
    if kind == "aikey":
        atomic_private(root / "identity.json", '{"mac":"02000000DEAD"}')
        message = "snapshot_state_identity_mismatch"
    else:
        state = json.loads((root / "aiport-adoption.json").read_text())
        state["controller_pin"] = "b" * 64
        atomic_private(root / "aiport-adoption.json", json.dumps(state))
        message = "snapshot_adoption_binding_mismatch"
    with pytest.raises(snapshot.SnapshotError, match=message):
        snapshot.backup(root, tmp_path / "mixed.tar", kind=kind,
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert not (tmp_path / "mixed.tar").exists()


@pytest.mark.parametrize("matches", [True, False])
def test_factory_certificate_binding_is_retained_or_refused_not_recreated(tmp_path, matches):
    root = source(tmp_path, "aiport")
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert((root / "device.crt").read_text())).hexdigest()
    record = {"mac": "02000000F004", "certificate_sha256": digest if matches else "0" * 64}
    atomic_private(root / "identity.json", json.dumps(record))
    output = tmp_path / "factory.tar"
    revision = hashlib.sha256((root / "config.json").read_bytes()).hexdigest()
    if matches:
        snapshot.backup(root, output, kind="aiport", expected_revision=revision)
        assert output.is_file()
    else:
        with pytest.raises(snapshot.SnapshotError, match="snapshot_state_certificate_mismatch"):
            snapshot.backup(root, output, kind="aiport", expected_revision=revision)
        assert not output.exists()
    assert json.loads((root / "identity.json").read_text()) == record


def test_unsupported_encrypted_state_key_never_opens_a_password_prompt(tmp_path):
    from cryptography.hazmat.primitives import serialization
    root = source(tmp_path, "aikey")
    key = serialization.load_pem_private_key((root / "device.key").read_bytes(), None)
    encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"fictional-fixture-only-passphrase"))
    atomic_private(root / "device.key", encrypted)
    started = time.monotonic()
    with pytest.raises(snapshot.SnapshotError, match="snapshot_certificate_key_mismatch"):
        snapshot.backup(root, tmp_path / "unsupported.tar", kind="aikey",
            expected_revision=hashlib.sha256((root / "config.json").read_bytes()).hexdigest())
    assert time.monotonic() - started < 1 and not (tmp_path / "unsupported.tar").exists()

"""Host-only adapters for already supported maintenance tools.

Search restore here ALWAYS means an isolated scratch restore/verification, not
overwriting the live index. No default live restore or NAS image swap is added.
"""

import hashlib
import dataclasses
import json
from pathlib import Path
import re
import secrets

from . import admin_maintenance as maintenance
from . import search_backup
from .maintenance_hash import artifact_digest


def prepare_search(host: Path, bridge: Path, *, kind: str, config: Path,
                   container: str, engine: Path, output: Path,
                   manifest: Path | None = None, profile: Path | None = None) -> str:
    if (kind not in {"backup", "restore"} or not isinstance(container, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", container)
            or any(not Path(p).is_absolute() for p in (config, engine, output))):
        raise maintenance.MaintenanceError("invalid_search_maintenance_plan")
    guards = [str(config)]
    if profile is not None:
        if not Path(profile).is_absolute():
            raise maintenance.MaintenanceError("invalid_search_maintenance_plan")
        guards.append(str(profile))
    if kind == "restore":
        if manifest is None or not Path(manifest).is_absolute() or Path(manifest).parent != Path(output):
            raise maintenance.MaintenanceError("invalid_search_maintenance_plan")
        value = json.loads(maintenance._read(manifest))
        if (not isinstance(value, dict) or not isinstance(value.get("dump"), str)
                or not re.fullmatch(r"search-[0-9]{8}T[0-9]{6}\.dump", value["dump"])
                or not isinstance(value.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
            raise maintenance.MaintenanceError("invalid_search_maintenance_backup")
        guards.append(str(manifest))
    revision = hashlib.sha256(maintenance._read(config)).hexdigest()
    inputs = {"kind": kind, "container": container, "engine": str(engine), "approval_nonce": secrets.token_hex(16),
              "engine_sha256": hashlib.sha256(maintenance._read(engine, 64 * 1024 * 1024)).hexdigest(),
              "output": str(output), "manifest": str(manifest) if manifest is not None else None,
              "profile": str(profile) if profile is not None else None}
    return maintenance.prepare(host, bridge, script=Path(__file__).with_name("maintenance_search_runbook.py"),
        inputs=inputs, kind=kind, profile="aikey", revision=revision, timeout=1800,
        expected_effect="isolated_restore", guard_files=guards,
        resources={"search_backup_backend.py": maintenance._read(Path(search_backup.__file__)).decode()})


def prepare_apple_update(host: Path, bridge: Path, *, config: Path, state_dir: Path,
                         service: str, new_name: str, image: str, kind: str,
                         health_url: str, certificate: Path, recovery_archive: Path,
                         streams: int | None = None, profile: str | None = None,
                         engine: Path = Path("/usr/local/bin/container")) -> str:
    """Publish the existing Apple swap only after a read-only exact host plan.

    The retained recovery archive is digest-bound, not claimed to have passed
    a full restore drill. NAS/Compose update and live database restore are not
    supported by this adapter.
    """
    from . import apple_supervisor, apple_upgrade
    from .admin_runtime import _target
    import ssl
    import subprocess
    profile = kind if profile is None else profile
    if (kind not in {"aikey", "aiport"} or (kind == "aiport" and
            (type(streams) is not int or not 1 <= streams <= 5))
            or not isinstance(image, str) or not re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", image)
            or any(not Path(p).is_absolute() for p in (config, state_dir, certificate, recovery_archive, engine))):
        raise maintenance.MaintenanceError("invalid_apple_update_plan")
    if (kind == "aikey" and profile != "aikey" or kind == "aiport" and
            (not isinstance(profile, str) or not re.fullmatch(r"aiport(?::[a-z0-9-]{1,32})?", profile))):
        raise maintenance.MaintenanceError("invalid_apple_profile_binding")
    services, _ = apple_supervisor.load_spec(state_dir / apple_supervisor.SPEC_FILE)
    selected = next((entry for entry in services if entry.name == service), None)
    if selected is None or Path(selected.state_source).resolve() != config.parent.resolve():
        raise maintenance.MaintenanceError("invalid_apple_profile_binding")
    _target(health_url)
    revision = hashlib.sha256(maintenance._read(config)).hexdigest()

    def run(argv):
        if argv[0] != apple_upgrade.CONTAINER:
            raise maintenance.MaintenanceError("unsupported_engine_operation")
        result = subprocess.run([str(engine), *argv[1:]], capture_output=True,
                                text=True, timeout=30, check=False)
        return result.returncode, result.stdout

    planned = apple_upgrade.plan(state_dir, service, new_name, image, run=run)
    certificate_bytes = maintenance._read(certificate).decode()
    first = re.search(r"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----", certificate_bytes)
    if first is None:
        raise maintenance.MaintenanceError("invalid_apple_update_certificate")
    own = re.search(r"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----",
                    maintenance._read(config.parent / "device.crt").decode())
    if own is None or ssl.PEM_cert_to_DER_cert(first.group()) != ssl.PEM_cert_to_DER_cert(own.group()):
        raise maintenance.MaintenanceError("invalid_apple_profile_certificate")
    inputs = {"state_dir": str(state_dir), "service": service, "new_name": new_name, "approval_nonce": secrets.token_hex(16),
              "image": image, "kind": kind, "streams": streams,
              "health_url": health_url, "certificate": str(certificate),
              "certificate_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(first.group())).hexdigest(),
              "engine": str(engine), "engine_sha256": artifact_digest(engine),
              "recovery_archive": str(recovery_archive), "recovery_sha256": artifact_digest(recovery_archive),
              "plan_sha256": hashlib.sha256(json.dumps(dataclasses.asdict(planned), sort_keys=True).encode()).hexdigest()}
    package = Path(__file__).parent
    resources = {"aikey/" + name: maintenance._read(package / name).decode()
                 for name in ("__init__.py", "apple_supervisor.py", "apple_upgrade.py", "maintenance_hash.py")}
    return maintenance.prepare(host, bridge, script=package / "maintenance_apple_runbook.py",
        inputs=inputs, kind="update", profile=profile, revision=revision, timeout=600,
        expected_effect="offline_replace",
        guard_files=[str(config), str(state_dir / apple_supervisor.SPEC_FILE), str(certificate),
                     str(config.parent / "device.crt")], resources=resources)


def prepare_state(host: Path, bridge: Path, *, kind: str, profile_kind: str,
                  directory: Path, archive: Path, container: str, engine: Path,
                  destination: Path | None = None, profile: str | None = None) -> str:
    """Full device files only; backup requires cold writers, restore stays offline."""
    from . import state_snapshot
    profile = profile_kind if profile is None else profile
    if (kind not in {"backup", "restore"} or profile_kind not in {"aikey", "aiport"}
            or not isinstance(container, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", container)
            or any(not Path(p).is_absolute() for p in (directory, archive, engine))
            or (kind == "restore" and (destination is None or not Path(destination).is_absolute()))):
        raise maintenance.MaintenanceError("invalid_state_maintenance_plan")
    if (profile_kind == "aikey" and profile != "aikey" or profile_kind == "aiport" and
            (not isinstance(profile, str) or not re.fullmatch(r"aiport(?::[a-z0-9-]{1,32})?", profile))):
        raise maintenance.MaintenanceError("invalid_state_profile_binding")
    directory, archive, engine = Path(directory), Path(archive), Path(engine).resolve(strict=True)
    maintenance._directory(directory)
    if directory == archive.parent or directory in archive.parents:
        raise maintenance.MaintenanceError("snapshot_archive_inside_profile")
    config = maintenance._read(directory / "config.json")
    if kind == "backup":
        names, entries = set(), 0
        for path in directory.rglob("*"):
            entries += 1
            if entries >= state_snapshot.MAX_FILES:
                raise maintenance.MaintenanceError("snapshot_entry_capacity")
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise maintenance.MaintenanceError("snapshot_special_file_refused")
            if path.is_file():
                names.add(path.relative_to(directory).as_posix())
        state_snapshot._required(json.loads(config), profile_kind, names, str(directory.absolute()))
    identity = state_snapshot._identity(json.loads(config), profile_kind, maintenance._read(directory / "device.crt"))
    revision = hashlib.sha256(config).hexdigest()
    inputs = {"kind": kind, "profile_kind": profile_kind, "directory": str(directory),
              "archive": str(archive), "container": container, "engine": str(engine),
              "engine_sha256": artifact_digest(engine), "identity": identity, "revision": revision,
              "destination": str(destination) if destination is not None else None,
              "approval_nonce": secrets.token_hex(16)}
    if kind == "restore":
        inputs["archive_sha256"] = artifact_digest(archive, state_snapshot.MAX_ARCHIVE_BYTES)
        with state_snapshot._regular(archive, state_snapshot.MAX_ARCHIVE_BYTES) as source:
            manifest = state_snapshot.inspect_archive(source, identity=identity)
        inputs["backup_revision"] = manifest["revision"]
        if Path(destination) == directory or directory in Path(destination).parents or Path(destination) in directory.parents:
            raise maintenance.MaintenanceError("snapshot_destination_overlaps_live_profile")
    elif archive.exists():
        raise maintenance.MaintenanceError("snapshot_archive_already_exists")
    package = Path(__file__).parent
    resources = {"aikey/" + name: maintenance._read(package / name).decode()
                 for name in ("__init__.py", "state_snapshot.py", "state_schema.py", "aiport_adoption.py",
                              "file_lock.py", "maintenance_hash.py")}
    return maintenance.prepare(host, bridge, script=package / "maintenance_state_runbook.py", inputs=inputs,
        kind=kind, profile=profile, revision=revision, timeout=1800,
        expected_effect="private_backup_export" if kind == "backup" else "isolated_restore",
        guard_files=[str(directory / "config.json"), str(directory / "device.crt")], resources=resources)

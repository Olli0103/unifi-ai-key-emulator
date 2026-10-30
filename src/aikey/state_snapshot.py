"""Cold device-file snapshots and restore to a new offline directory.

No database, model download, process stop/start, or live-directory replacement.
The host adapter must hold the profile writer lock and verify no running
container mounts the state directory before and after a snapshot.
"""

from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
import tarfile
import tempfile
import unicodedata

from .aiport_adoption import validate_adoption_state
from .state_schema import check_schema

SCHEMA = "aikey-cold-state/1"
MAX_BYTES = 1024**3
MAX_FILES = 200000
MAX_MANIFEST_BYTES = 64 * 1024**2
MAX_ARCHIVE_BYTES = MAX_BYTES + 320 * 1024**2
MANIFEST = "_aikey_snapshot_manifest.json"


class SnapshotError(ValueError):
    pass


class _SnapshotMember(tarfile.TarInfo):
    @classmethod
    def fromtarfile(cls, archive):
        # Some maintained Python versions bypass the public frombuf override
        # in their default fromtarfile. Validate the header explicitly here.
        member = cls.frombuf(
            archive.fileobj.read(tarfile.BLOCKSIZE), archive.encoding, archive.errors
        )
        member.offset = archive.fileobj.tell() - tarfile.BLOCKSIZE
        return member._proc_member(archive)

    @classmethod
    def frombuf(cls, buf, encoding, errors):
        member = super().frombuf(buf, encoding, errors)
        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}:
            raise SnapshotError("snapshot_extension_or_link_refused")
        return member


class _SnapshotTar(tarfile.TarFile):
    """No redundant TarInfo cache for a sequential, digest-checked archive."""

    def addfile(self, *args, **kwargs):
        super().addfile(*args, **kwargs)
        self.members.clear()

    def next(self):
        member = super().next()
        self.members.clear()
        return member


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\x00" in value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or PurePosixPath(value).is_absolute()
    ):
        raise SnapshotError("invalid_snapshot_member")
    return value


def _object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SnapshotError("duplicate_snapshot_field")
            result[key] = value
        return result

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            raise SnapshotError("invalid_snapshot_number")
        return result

    value = json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_float=floating,
        parse_constant=lambda value: (_ for _ in ()).throw(
            SnapshotError("invalid_snapshot_number")
        ),
    )
    if not isinstance(value, dict):
        raise SnapshotError("invalid_snapshot_object")
    return value


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _identity(config, kind, certificate):
    mac = (config.get("device") or {}).get("mac") if kind == "aikey" else config.get("mac")
    if not isinstance(mac, str) or not re.fullmatch(r"[0-9a-fA-F]{12}", mac.replace(":", "")):
        raise SnapshotError("snapshot_identity_unavailable")
    return hashlib.sha256(
        _encode([kind, mac.replace(":", "").upper(), hashlib.sha256(certificate).hexdigest()])
    ).hexdigest()


def _required(config, kind, files, state_root):
    required = {"config.json", "device.crt", "device.key"}
    required.add(
        "device-state.json"
        if kind == "aikey" and "device-state.json" in files
        else "identity.json"
        if kind == "aikey"
        else "aiport-adoption.json"
        if "aiport-adoption.json" in files
        else "identity.json"
    )
    if kind == "aiport":
        required.add("controller-ca.pem")
    if kind == "aikey" and (config.get("search") or {}).get("enabled"):
        required.add("search-profile.json")
    runtime = (config.get("runtime") or {}).get("state_dir") if kind == "aikey" else "/state"

    def references(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if (name.endswith("_file") or name == "sslrootcert") and item is not None:
                    if not isinstance(item, str) or not item:
                        raise SnapshotError("invalid_snapshot_reference")
                    path = PurePosixPath(item)
                    if path.is_absolute():
                        for root in (runtime, state_root):
                            try:
                                path = path.relative_to(PurePosixPath(root))
                                break
                            except (ValueError, TypeError):
                                pass
                        else:
                            raise SnapshotError(
                                "external_snapshot_reference_not_supported"
                            ) from None
                    required.add(_name(path.as_posix()))
                elif isinstance(item, (dict, list)):
                    references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(config)
    if required - set(files):
        raise SnapshotError("snapshot_required_state_missing")


@contextmanager
def _regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise SnapshotError("snapshot_not_bounded_regular")
        yield source


def _digest(source, size):
    source.seek(0)
    digest, total = hashlib.sha256(), 0
    while raw := source.read(min(1024 * 1024, size - total + 1)):
        total += len(raw)
        if total > size:
            raise SnapshotError("snapshot_input_changed")
        digest.update(raw)
    if total != size:
        raise SnapshotError("snapshot_input_changed")
    return digest.hexdigest()


def inspect_archive(source, *, identity=None, revision=None):
    """Verify one retained descriptor completely before any restore path exists."""
    source.seek(0)
    files, directories, manifest, total, config, certificate, key = (
        {},
        [],
        None,
        0,
        None,
        None,
        None,
    )
    identity_records, directory_names = {}, set()
    with _SnapshotTar.open(fileobj=source, mode="r:", tarinfo=_SnapshotMember) as archive:
        for member in archive:
            name = _name(member.name)
            if (
                not (member.isfile() or member.isdir())
                or name in files
                or name in directory_names
                or manifest is not None
                or len(files) + len(directories) >= MAX_FILES
                or member.size < 0
                or member.size > MAX_BYTES - total
                or member.mode & ~0o700
            ):
                raise SnapshotError("invalid_snapshot_archive")
            if member.isdir():
                if member.size != 0:
                    raise SnapshotError("invalid_snapshot_directory")
                directories.append(name)
                directory_names.add(name)
                continue
            if name == MANIFEST:
                if member.size > MAX_MANIFEST_BYTES:
                    raise SnapshotError("snapshot_manifest_too_large")
                manifest = _object(archive.extractfile(member).read())
                continue
            total += member.size
            digest = hashlib.sha256()
            selected = (
                bytearray()
                if name
                in {
                    "config.json",
                    "device.crt",
                    "device.key",
                    "identity.json",
                    "device-state.json",
                    "aiport-adoption.json",
                }
                else None
            )
            if selected is not None and member.size > 1024 * 1024:
                raise SnapshotError("snapshot_metadata_too_large")
            with archive.extractfile(member) as content:
                while raw := content.read(1024 * 1024):
                    digest.update(raw)
                    if selected is not None:
                        selected.extend(raw)
            if name == "config.json":
                config = _object(bytes(selected))
            elif name == "device.crt":
                certificate = bytes(selected)
            elif name == "device.key":
                key = bytes(selected)
            elif name in {"identity.json", "device-state.json", "aiport-adoption.json"}:
                identity_records[name] = _object(bytes(selected))
            files[name] = {"sha256": digest.hexdigest(), "bytes": member.size, "mode": member.mode}
    second_end = source.read(512)
    if len(second_end) != 512 or any(second_end):
        raise SnapshotError("snapshot_end_marker_invalid")
    for trailing in iter(lambda: source.read(1024 * 1024), b""):
        if any(trailing):
            raise SnapshotError("snapshot_trailing_payload")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "kind", "identity", "revision", "files", "directories", "state_root"}
        or manifest["schema"] != SCHEMA
        or manifest["kind"] not in ("aikey", "aiport")
        or manifest["files"] != files
        or manifest["directories"] != directories
        or not isinstance(manifest["state_root"], str)
        or not Path(manifest["state_root"]).is_absolute()
        or config is None
        or certificate is None
        or key is None
    ):
        raise SnapshotError("snapshot_manifest_mismatch")
    for name, entry in manifest["files"].items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "bytes", "mode"}
            or type(entry["bytes"]) is not int
            or type(entry["mode"]) is not int
            or not isinstance(entry["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise SnapshotError("snapshot_manifest_mismatch")
    if (
        _identity(config, manifest["kind"], certificate) != manifest["identity"]
        or files["config.json"]["sha256"] != manifest["revision"]
        or identity is not None
        and identity != manifest["identity"]
        or revision is not None
        and revision != manifest["revision"]
    ):
        raise SnapshotError("snapshot_identity_or_revision_mismatch")
    _required(config, manifest["kind"], files, manifest["state_root"])
    names = set(files) | set(directories)
    if len({unicodedata.normalize("NFC", name).casefold() for name in names}) != len(names):
        raise SnapshotError("snapshot_name_collision")
    for name in names:
        if any(
            parent.as_posix() != "." and parent.as_posix() not in directory_names
            for parent in PurePosixPath(name).parents
        ):
            raise SnapshotError("snapshot_parent_directory_missing")
    mac = (
        (config["device"]["mac"] if manifest["kind"] == "aikey" else config["mac"])
        .replace(":", "")
        .upper()
    )
    for name in ("identity.json", "device-state.json"):
        if name in identity_records:
            value = identity_records[name]
            if (
                not isinstance(value.get("mac"), str)
                or value["mac"].replace(":", "").upper() != mac
            ):
                raise SnapshotError("snapshot_state_identity_mismatch")
            if name == "device-state.json":
                try:
                    check_schema(value)
                except ValueError:
                    raise SnapshotError("snapshot_state_schema_unavailable") from None
                if type(value.get("adopted")) is not bool:
                    raise SnapshotError("snapshot_state_identity_mismatch")
            if "certificate_sha256" in value:
                first = re.search(
                    rb"-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----", certificate
                )
                digest = (
                    hashlib.sha256(ssl.PEM_cert_to_DER_cert(first.group().decode())).hexdigest()
                    if first
                    else None
                )
                if value["certificate_sha256"] != digest:
                    raise SnapshotError("snapshot_state_certificate_mismatch")
    if "aiport-adoption.json" in identity_records:
        try:
            validate_adoption_state(
                identity_records["aiport-adoption.json"],
                {
                    "controller_ip": config.get("controller_ip"),
                    "controller_pin": config.get("controller_pin"),
                    "control_port": config.get("control_port", 7442),
                },
            )
        except ValueError:
            raise SnapshotError("snapshot_adoption_binding_mismatch") from None
    try:
        with tempfile.TemporaryFile() as cert_file, tempfile.TemporaryFile() as key_file:
            cert_file.write(certificate)
            cert_file.flush()
            key_file.write(key)
            key_file.flush()
            cert_file.seek(0)
            key_file.seek(0)
            def refuse_password_prompt():
                raise SnapshotError("snapshot_encrypted_key_not_supported")
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
                f"/dev/fd/{cert_file.fileno()}", f"/dev/fd/{key_file.fileno()}",
                password=refuse_password_prompt,
            )
    except (OSError, ValueError, ssl.SSLError):
        raise SnapshotError("snapshot_certificate_key_mismatch") from None
    return manifest


def backup(directory: Path, output: Path, *, kind: str, expected_revision: str) -> dict:
    """Caller holds cold/profile lock; export all regular files, never omit one."""
    directory, output = Path(directory), Path(output)
    parent_info = output.parent.lstat()
    if (
        kind not in ("aikey", "aiport")
        or output.exists()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_mode & 0o077
    ):
        raise SnapshotError("invalid_snapshot_destination")
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = os.fstat(parent)
    if (opened.st_dev, opened.st_ino) != (parent_info.st_dev, parent_info.st_ino):
        os.close(parent)
        raise SnapshotError("snapshot_destination_path_changed")
    root = -1
    files, directories, total, selected = {}, [], 0, {}
    try:
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with tempfile.TemporaryFile(dir=output.parent) as target:
            with _SnapshotTar.open(fileobj=target, mode="w:", format=tarfile.USTAR_FORMAT) as archive:

                def visit(fd, prefix="", depth=0):
                    nonlocal total
                    if depth > 24:
                        raise SnapshotError("snapshot_depth_exceeded")
                    for name in sorted(os.listdir(fd)):
                        relative = _name(prefix + name)
                        if relative == MANIFEST:
                            raise SnapshotError("reserved_snapshot_member")
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        child = os.open(
                            name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            actual = os.fstat(child)
                            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                                raise SnapshotError("snapshot_input_changed")
                            if stat.S_ISDIR(actual.st_mode):
                                if len(files) + len(directories) >= MAX_FILES - 1:
                                    raise SnapshotError("snapshot_size_or_alias_refused")
                                directories.append(relative)
                                member = tarfile.TarInfo(relative)
                                member.type, member.mode = tarfile.DIRTYPE, 0o700
                                archive.addfile(member)
                                visit(child, relative + "/", depth + 1)
                            elif stat.S_ISREG(actual.st_mode):
                                if (
                                    actual.st_nlink != 1
                                    or len(files) + len(directories) >= MAX_FILES - 1
                                    or actual.st_size > MAX_BYTES - total
                                ):
                                    raise SnapshotError("snapshot_size_or_alias_refused")
                                with os.fdopen(os.dup(child), "rb") as content:
                                    digest = _digest(content, actual.st_size)
                                    content.seek(0)
                                    member = tarfile.TarInfo(relative)
                                    member.size, member.mode = (
                                        actual.st_size,
                                        stat.S_IMODE(actual.st_mode) & 0o700,
                                    )

                                    class Copied:
                                        digest = hashlib.sha256()

                                        def read(self, size):
                                            raw = content.read(size)
                                            self.digest.update(raw)
                                            return raw

                                    copied = Copied()
                                    archive.addfile(member, copied)
                                    if (
                                        copied.digest.hexdigest() != digest
                                        or _digest(content, actual.st_size) != digest
                                    ):
                                        raise SnapshotError("snapshot_input_changed")
                                    if relative in {"config.json", "device.crt"}:
                                        if actual.st_size > 1024 * 1024:
                                            raise SnapshotError("snapshot_metadata_too_large")
                                        content.seek(0)
                                        selected[relative] = content.read()
                                total += actual.st_size
                                files[relative] = {
                                    "sha256": digest,
                                    "bytes": actual.st_size,
                                    "mode": member.mode,
                                }
                            else:
                                raise SnapshotError("snapshot_special_file_refused")
                        finally:
                            os.close(child)

                visit(root)
                config = _object(selected.get("config.json", b"{}"))
                manifest = {
                    "schema": SCHEMA,
                    "kind": kind,
                    "files": files,
                    "directories": directories,
                    "state_root": str(directory.absolute()),
                    "revision": files.get("config.json", {}).get("sha256"),
                    "identity": _identity(config, kind, selected.get("device.crt", b"")),
                }
                if manifest["revision"] != expected_revision:
                    raise SnapshotError("snapshot_revision_changed")
                _required(config, kind, files, manifest["state_root"])
                raw = _encode(manifest)
                member = tarfile.TarInfo(MANIFEST)
                member.size = len(raw)
                member.mode = 0o600
                archive.addfile(member, io.BytesIO(raw))
                del raw
            target.flush()
            os.fsync(target.fileno())
            inspect_archive(target, identity=manifest["identity"], revision=expected_revision)
            digest = _digest(target, os.fstat(target.fileno()).st_size)
            try:
                published = os.open(
                    output.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                with os.fdopen(published, "w+b") as destination:
                    target.seek(0)
                    for raw in iter(lambda: target.read(1024 * 1024), b""):
                        destination.write(raw)
                    destination.flush()
                    os.fsync(destination.fileno())
                    info = os.fstat(destination.fileno())
                    alias = os.stat(output.name, dir_fd=parent, follow_symlinks=False)
                    if (alias.st_dev, alias.st_ino) != (info.st_dev, info.st_ino) or _digest(
                        destination, info.st_size
                    ) != digest:
                        raise SnapshotError("snapshot_publication_uncertain")
                os.fsync(parent)
            finally:
                alias = output.parent.lstat()
                if (alias.st_dev, alias.st_ino) != (opened.st_dev, opened.st_ino):
                    raise SnapshotError("snapshot_destination_path_changed")
    finally:
        if root >= 0:
            os.close(root)
        os.close(parent)
    return {
        "sha256": digest,
        "files": len(files),
        "identity": manifest["identity"],
        "revision": expected_revision,
    }


def restore(
    archive: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_identity: str,
    expected_revision: str,
) -> dict:
    """Restore only to a fresh offline directory, never an active/live tree."""
    destination = Path(destination)
    parent_info = destination.parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_mode & 0o077
    ):
        raise SnapshotError("snapshot_destination_not_private")
    if destination.exists() or destination.is_symlink():
        raise SnapshotError("snapshot_destination_exists")
    # Immutable anonymous copy; verification and extraction use this same FD,
    # even if the original archive pathname/inode changes during the operation.
    with (
        _regular(archive, MAX_ARCHIVE_BYTES) as original,
        tempfile.TemporaryFile(dir=destination.parent) as snapshot,
    ):
        total, digest = 0, hashlib.sha256()
        while raw := original.read(1024 * 1024):
            total += len(raw)
            if total > MAX_ARCHIVE_BYTES:
                raise SnapshotError("snapshot_archive_too_large")
            snapshot.write(raw)
            digest.update(raw)
        snapshot.flush()
        if digest.hexdigest() != expected_sha256:
            raise SnapshotError("snapshot_digest_mismatch")
        manifest = inspect_archive(snapshot, identity=expected_identity, revision=expected_revision)
        parent_root = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(parent_root)
        if (opened.st_dev, opened.st_ino) != (parent_info.st_dev, parent_info.st_ino):
            os.close(parent_root)
            raise SnapshotError("snapshot_destination_path_changed")
        root = -1
        try:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent_root)
            root = os.open(
                destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_root
            )
            snapshot.seek(0)
            with _SnapshotTar.open(fileobj=snapshot, mode="r:", tarinfo=_SnapshotMember) as bundle:
                for member in bundle:
                    if member.name == MANIFEST:
                        continue
                    parts = PurePosixPath(member.name).parts
                    parent = os.dup(root)
                    try:
                        for part in parts if member.isdir() else parts[:-1]:
                            try:
                                os.mkdir(part, 0o700, dir_fd=parent)
                            except FileExistsError:
                                pass
                            child = os.open(
                                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                            )
                            os.close(parent)
                            parent = child
                        if member.isdir():
                            os.fsync(parent)
                            continue
                        fd = os.open(
                            parts[-1],
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            member.mode,
                            dir_fd=parent,
                        )
                        with os.fdopen(fd, "wb") as target, bundle.extractfile(member) as content:
                            for raw in iter(lambda: content.read(1024 * 1024), b""):
                                target.write(raw)
                            target.flush()
                            os.fsync(target.fileno())
                        os.fsync(parent)
                    finally:
                        os.close(parent)
            # Read-back every restored byte, from the retained directory FD.
            for name, item in manifest["files"].items():
                parent = os.dup(root)
                try:
                    parts = PurePosixPath(name).parts
                    for part in parts[:-1]:
                        child = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                        )
                        os.close(parent)
                        parent = child
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                    with os.fdopen(fd, "rb") as content:
                        if _digest(content, item["bytes"]) != item["sha256"]:
                            raise SnapshotError("snapshot_restore_readback_failed")
                finally:
                    os.close(parent)
            os.fsync(root)
            info = os.fstat(root)
            alias = os.stat(destination.name, dir_fd=parent_root, follow_symlinks=False)
            outer = destination.parent.lstat()
            if (info.st_dev, info.st_ino) != (alias.st_dev, alias.st_ino) or (
                outer.st_dev,
                outer.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise SnapshotError("snapshot_destination_path_changed")
        finally:
            if root >= 0:
                os.close(root)
            os.close(parent_root)
    # On any post-creation failure, leave the private partial tree for
    # reconciliation; never recursively delete through a replaced pathname.
    return {"restored": True, "files": len(manifest["files"]), "activated": False}

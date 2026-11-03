"""Private face identities for the AI Key's local face recognition (#20).

Templates are SFace embeddings kept in ``<state>/faces/identities.json``
(directory 0700, file 0600). This store does not transmit or log templates;
separate backup and enrollment-server copies need their own policy. Every
identity can be explicitly deleted, or all of them purged. Matching uses
cosine similarity with OpenCV's recommended SFace threshold; a face below
it stays unknown rather than being given the nearest name.

New templates use schema 2, bound to an explicit encoder/pipeline digest.
Nonempty unbound schema-1 stores are metadata/deletion-only until explicit
removal and re-enrollment; no vector conversion or automatic relabelling.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import stat
import sys
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from .provider_http import provider_opener
from .provider_network import ProviderAddressError, checked_addresses, validate_provider_host
from .protocol import _json_object
from .model_work_fence import ModelWorkFence, ModelWorkFenceError


MATCH_THRESHOLD = 0.363          # OpenCV SFace cosine threshold
_DIMENSION = 128
_MAX_IDENTITIES = 50
_MAX_TEMPLATES = 20
_MAX_EMBED_REPLY_BYTES = 256 * 1024
_MAX_STORE_BYTES = 8 * 1024 * 1024
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,64}\Z")


def checked_embedding_space(value) -> str:
    """Opaque encoder/pipeline digest, not a claim of recognition quality."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise FaceStoreError("face_embedding_space_invalid")
    return value


class FaceStoreError(ValueError):
    """Fixed failure without names, images or embeddings."""


class FaceStoreRevisionConflict(FaceStoreError):
    """Saved templates changed since the operator's observation."""


class FaceStoreOutcomeUncertain(FaceStoreError):
    """A write may have committed; inspect state without automatic retry."""


_DIRECT_READ = object()
_MISSING_REVISION = hashlib.sha256(b"local-aikey-face-store/missing/1").hexdigest()


def _unit(embedding) -> list[float]:
    try:
        valid = (isinstance(embedding, list) and len(embedding) == _DIMENSION
                 and all(type(v) in (int, float) and math.isfinite(v) for v in embedding))
    except OverflowError:
        valid = False
    if not valid:
        raise FaceStoreError("invalid_embedding")
    scale = max(abs(v) for v in embedding)
    if not scale:
        raise FaceStoreError("invalid_embedding")
    scaled = [v / scale for v in embedding]
    norm = math.hypot(*scaled)
    return [v / norm for v in scaled]


class FaceStore:
    def __init__(self, state_dir: Path):
        self.directory = Path(state_dir) / "faces"
        self.path = self.directory / "identities.json"

    @staticmethod
    def _directory_info(descriptor, *, private):
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & (0o077 if private else 0o022)):
            raise FaceStoreError("face_store_not_private")
        return info

    def _bound(self, root, directory):
        """Keep the observed roots, not a replacement symlink/path alias."""
        try:
            found = os.stat(self.directory.parent, follow_symlinks=False)
            opened = os.fstat(root)
            if not stat.S_ISDIR(found.st_mode) or (found.st_dev, found.st_ino) != (opened.st_dev, opened.st_ino):
                return False
            if directory is not None:
                found = os.stat("faces", dir_fd=root, follow_symlinks=False)
                opened = os.fstat(directory)
                return (stat.S_ISDIR(found.st_mode)
                        and (found.st_dev, found.st_ino) == (opened.st_dev, opened.st_ino))
            return True
        except OSError:
            return False

    @contextmanager
    def _access(self, *, writer=False, create=False):
        from .file_lock import acquire

        root = directory = lock = None
        try:
            try:
                root = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            except FileNotFoundError:
                if writer:
                    raise FaceStoreError("face_store_state_root_missing") from None
                yield None
                return
            self._directory_info(root, private=False)
            if writer:
                lock = os.open(".face-store.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                               | os.O_NONBLOCK, 0o600, dir_fd=root)
                meta = os.fstat(lock)
                if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                        or meta.st_mode & 0o077 or meta.st_nlink != 1):
                    raise FaceStoreError("face_store_not_private")
                try:
                    acquire(lock, timeout=5)
                except TimeoutError:
                    raise FaceStoreError("face_store_busy") from None
            if create:
                try:
                    os.mkdir("faces", mode=0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError:
                    pass
            try:
                directory = os.open("faces", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                directory = None
            if directory is not None:
                self._directory_info(directory, private=True)
            if not self._bound(root, directory):
                raise FaceStoreError("face_store_unavailable")
            yield directory
            if not self._bound(root, directory):
                failure = FaceStoreOutcomeUncertain if writer else FaceStoreError
                raise failure("face_store_write_uncertain" if writer else "face_store_unavailable")
        except OSError:
            raise FaceStoreError("face_store_unavailable") from None
        finally:
            for descriptor in (directory, lock, root):
                if descriptor is not None:
                    os.close(descriptor)

    @staticmethod
    def _bytes(directory) -> bytes | None:
        if directory is None:
            return None
        try:
            descriptor = os.open("identities.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError:
            raise FaceStoreError("face_store_unavailable") from None
        try:
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_nlink != 1):
                    raise FaceStoreError("face_store_not_private")
                if not 0 < info.st_size <= _MAX_STORE_BYTES:
                    raise FaceStoreError("face_store_invalid")
                raw = source.read(_MAX_STORE_BYTES + 1)
        except OSError:
            raise FaceStoreError("face_store_unavailable") from None
        if not 0 < len(raw) <= _MAX_STORE_BYTES:
            raise FaceStoreError("face_store_invalid")
        return raw

    @staticmethod
    def _parse(raw) -> dict:
        if raw is None:
            return {"schema": 1, "identities": {}}
        try:
            data = _json_object(raw)
            _validate_store(data)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise FaceStoreError("face_store_invalid") from None
        return data

    def _load(self, directory_fd=_DIRECT_READ) -> dict:
        if directory_fd is _DIRECT_READ:
            with self._access() as directory:
                return self._load(directory_fd=directory)
        return self._parse(self._bytes(directory_fd))

    @staticmethod
    def _revision(raw):
        return _MISSING_REVISION if raw is None else hashlib.sha256(raw).hexdigest()

    def snapshot(self) -> dict:
        """Private metadata only, never vectors; one captured atomic record."""
        with self._access() as directory:
            raw = self._bytes(directory)
            data = self._parse(raw)
            return {"revision": self._revision(raw), "identities": [
                {"name": name, "templates": len(entry["templates"]), "created": entry["created"]}
                for name, entry in sorted(data["identities"].items())]}

    def _check_revision(self, directory, expected):
        if expected is not None and (not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or expected != self._revision(self._bytes(directory))):
            raise FaceStoreRevisionConflict("face_store_changed")

    def enrollment_status(self):
        """Private enrollment preflight; no image or vector leaves the store."""
        with self._access() as directory:
            raw = self._bytes(directory)
            data = self._parse(raw)
            return {"revision": self._revision(raw),
                    "embedding_space": data.get("embedding_space"),
                    "migration_required": data["schema"] == 1 and bool(data["identities"]),
                    "identities": {name: len(entry["templates"])
                                   for name, entry in data["identities"].items()}}

    def _save(self, data: dict, *, directory_fd) -> None:
        _validate_store(data)
        encoded = json.dumps(data, separators=(",", ":")).encode("utf-8")
        temporary = ".faces-" + secrets.token_hex(16)
        created = committed = False
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            created = True
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, "identities.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            committed = True
            os.fsync(directory_fd)
            if self._bytes(directory_fd) != encoded:
                raise FaceStoreOutcomeUncertain("face_store_write_uncertain")
        except (OSError, FaceStoreError):
            raise FaceStoreOutcomeUncertain("face_store_write_uncertain") from None
        finally:
            if created and not committed:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    raise FaceStoreOutcomeUncertain("face_store_write_uncertain") from None

    def names(self) -> list[str]:
        return sorted(self._load()["identities"])

    def enroll(self, name: str, embedding, *, embedding_space=None,
               expected_revision: str | None = None) -> int:
        embedding_space = checked_embedding_space(embedding_space)
        if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
            raise FaceStoreError("invalid_name")
        name = name.strip()
        template = _unit(embedding)
        with self._access(writer=True, create=True) as directory:
            self._check_revision(directory, expected_revision)
            data = self._load(directory_fd=directory)
            if data["schema"] == 1 and not data["identities"]:
                data = {"schema": 2, "embedding_space": embedding_space, "identities": {}}
            self._check_space(data, embedding_space)
            identities = data["identities"]
            if name not in identities and len(identities) >= _MAX_IDENTITIES:
                raise FaceStoreError("too_many_identities")
            entry = identities.setdefault(name, {"templates": [], "created": int(time.time())})
            if len(entry["templates"]) >= _MAX_TEMPLATES:
                raise FaceStoreError("too_many_templates")
            entry["templates"].append(template)
            self._save(data, directory_fd=directory)
            return len(entry["templates"])

    def delete(self, name: str, *, expected_revision: str | None = None) -> bool:
        if not isinstance(name, str):
            raise FaceStoreError("invalid_name")
        with self._access(writer=True) as directory:
            self._check_revision(directory, expected_revision)
            data = self._load(directory_fd=directory)
            removed = data["identities"].pop(name, None) is not None
            if removed:
                self._save(data, directory_fd=directory)
            return removed

    def purge(self, *, expected_revision: str | None = None) -> None:
        with self._access(writer=True) as directory:
            self._check_revision(directory, expected_revision)
            if self._bytes(directory) is not None:
                try:
                    os.unlink("identities.json", dir_fd=directory)
                    os.fsync(directory)
                    if self._bytes(directory) is not None:
                        raise FaceStoreOutcomeUncertain("face_store_write_uncertain")
                except (OSError, FaceStoreError):
                    raise FaceStoreOutcomeUncertain("face_store_write_uncertain") from None

    @staticmethod
    def _check_space(data, embedding_space):
        if data["schema"] == 1:
            if data["identities"]:
                raise FaceStoreError("face_store_migration_required")
        elif data["embedding_space"] != embedding_space:
            raise FaceStoreError("face_embedding_space_mismatch")

    def match(self, embedding, *, embedding_space=None) -> tuple[str | None, list[tuple[str, float]]]:
        """Best identity at or above the threshold, plus up to three candidates."""
        embedding_space = checked_embedding_space(embedding_space)
        probe = _unit(embedding)
        data = self._load()
        self._check_space(data, embedding_space)
        scores = []
        for name, entry in data["identities"].items():
            best = max((sum(a * b for a, b in zip(probe, template))
                        for template in entry["templates"]), default=-1.0)
            if best >= MATCH_THRESHOLD:
                scores.append((name, round(best, 4)))
        scores.sort(key=lambda item: -item[1])
        return (scores[0][0] if scores else None), scores[:3]


def _validate_store(data):
    """Reject malformed saved state; never silently normalize/repair templates."""
    if (type(data.get("schema")) is not int or data["schema"] not in (1, 2)
            or set(data) != ({"schema", "identities"} if data["schema"] == 1
                             else {"schema", "embedding_space", "identities"})
            or not isinstance(data["identities"], dict)
            or len(data["identities"]) > _MAX_IDENTITIES):
        raise FaceStoreError("face_store_invalid")
    if data["schema"] == 2:
        checked_embedding_space(data["embedding_space"])
    for name, entry in data["identities"].items():
        if (not isinstance(name, str) or name != name.strip() or not _NAME.fullmatch(name)
                or not isinstance(entry, dict) or set(entry) != {"templates", "created"}
                or type(entry["created"]) is not int or not 0 <= entry["created"] <= 2 ** 63 - 1
                or not isinstance(entry["templates"], list)
                or not 1 <= len(entry["templates"]) <= _MAX_TEMPLATES):
            raise FaceStoreError("face_store_invalid")
        for template in entry["templates"]:
            if (not isinstance(template, list) or len(template) != _DIMENSION
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in template)
                    or not math.isclose(math.hypot(*template), 1.0, rel_tol=1e-6, abs_tol=1e-6)):
                raise FaceStoreError("face_store_invalid")


def _private_server(url: str) -> str:
    import ipaddress
    if not isinstance(url, str):
        raise FaceStoreError("face_server_must_be_local")
    try:
        parsed = urlsplit(url)
        validate_provider_host(parsed.hostname, authority=parsed.netloc)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port or 80
        if (parsed.scheme != 'http' or not (address.is_loopback or address.is_private)
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.port == 0 or not 1 <= port <= 65535
                or any(c.isspace() or c in '\\%' for c in url)):
            raise ValueError
        checked_addresses(parsed.hostname, port,
                          [{'hostname': parsed.hostname, 'host': parsed.hostname, 'port': port}])
    except (ProviderAddressError, ValueError, TypeError) as exc:
        raise FaceStoreError("face_server_must_be_local") from exc
    return url.rstrip("/") + "/v1/faces"


def embed_single_face(image: bytes, server: str) -> tuple[list[float], str]:
    """Exactly one face from an owner-supplied enrollment photo."""
    import urllib.request
    import uuid
    endpoint = _private_server(server)
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"enroll.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
    body += image + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "Accept-Encoding": "identity"})
    try:
        with provider_opener().open(request, timeout=60) as response:
            if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise FaceStoreError('enrollment_unavailable')
            raw = response.read(_MAX_EMBED_REPLY_BYTES + 1)
        if len(raw) > _MAX_EMBED_REPLY_BYTES:
            raise FaceStoreError('enrollment_unavailable')
        result = _json_object(raw)
        found = result.get('faces')
        if not isinstance(found, list) or len(found) != 1 or not isinstance(found[0], dict):
            raise FaceStoreError("enrollment_photo_needs_exactly_one_face")
        return _unit(found[0].get('embedding')), checked_embedding_space(result.get('embedding_space'))
    except FaceStoreError:
        raise
    except Exception:
        # HTTP errors, URLs, provider bodies and embeddings must not reach CLI
        # stderr. No retry, fallback or identity-store write follows failure.
        raise FaceStoreError('enrollment_unavailable') from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-aikey-faces")
    parser.add_argument("command", choices=("list", "enroll", "delete", "purge"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8179")
    args = parser.parse_args(argv)
    store = FaceStore(args.state_dir)
    try:
        if args.command == "list":
            print(json.dumps(store.names(), ensure_ascii=False))
        elif args.command == "enroll":
            if not args.name or not args.image:
                raise FaceStoreError("enroll needs --name and --image")
            fence = ModelWorkFence.from_state(args.state_dir)
            lease = None
            try:
                # Acquire asynchronously, then keep the synchronous request on
                # this owning CLI thread. No cancelled executor waiter can drop
                # exclusion while a detached enrollment request still runs.
                lease = asyncio.run(fence.acquire(deadline=time.monotonic() + 10))
                template, space = embed_single_face(args.image.read_bytes(), args.server)
            finally:
                if lease is not None:
                    lease.close()
                fence.close()
            count = store.enroll(args.name, template, embedding_space=space)
            print(json.dumps({"templates": count}))
        elif args.command == "delete":
            print(json.dumps({"deleted": store.delete(args.name or "")}))
        else:
            store.purge()
            print(json.dumps({"purged": True}))
    except (FaceStoreError, ModelWorkFenceError, OSError, ValueError, KeyError) as exc:
        print(f"local-aikey-faces: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

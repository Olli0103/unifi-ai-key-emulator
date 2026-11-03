"""Private, input-bound exported object crops for an index migration.

An operator exports existing, authorized *original* Protect object thumbnails;
this reader neither obtains camera permission nor invents an object/image join.
The manifest records the native object/event/camera/crop association. Every
image is content addressed, rehashed and decoded before it reaches an encoder.
No network, credentials, metadata logging, deletion or live-index writes.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat

from PIL import Image, UnidentifiedImageError

SCHEMA = "aikey-index-source-archive/1"
MAX_MANIFEST = 16 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
MAX_OBJECTS = 100_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class SourceArchiveError(RuntimeError):
    """Fixed diagnostics only; no private identifiers, paths or image bytes."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _private(meta, *, directory=False):
    predicate = stat.S_ISDIR if directory else stat.S_ISREG
    if (not predicate(meta.st_mode) or meta.st_uid != os.geteuid()
            or meta.st_mode & 0o077 or (not directory and meta.st_nlink != 1)):
        raise SourceArchiveError("The source archive is not owner-private")


def _read(directory, name, limit):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        meta = os.fstat(stream.fileno())
        _private(meta)
        if not 0 < meta.st_size <= limit:
            raise SourceArchiveError("A source archive member exceeds its bounds")
        value = stream.read(limit + 1)
        if len(value) != meta.st_size:
            raise SourceArchiveError("A source archive member changed while reading")
        after = os.fstat(stream.fileno())
        if (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SourceArchiveError("A source archive member changed while reading")
        return value


def _manifest(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(value, dict) or set(value) != {
                "schema", "source", "authorized_cameras", "objects"}
                or value["schema"] != SCHEMA
                or value["source"] != "protect-original-object-crops"
                or not isinstance(value["authorized_cameras"], list)
                or not 1 <= len(value["authorized_cameras"]) <= 256
                or any(not isinstance(x, str) or not _ID.fullmatch(x)
                       for x in value["authorized_cameras"])
                or len(set(value["authorized_cameras"])) != len(value["authorized_cameras"])
                or not isinstance(value["objects"], list)
                or not 0 <= len(value["objects"]) <= MAX_OBJECTS):
            raise ValueError
        rows = {}
        for row in value["objects"]:
            if (not isinstance(row, dict) or set(row) != {
                    "object_id", "event_id", "camera_id", "crop_id", "input_sha256"}
                    or any(not isinstance(row[k], str) or not _ID.fullmatch(row[k])
                           for k in ("object_id", "event_id", "camera_id", "crop_id"))
                    or not isinstance(row["input_sha256"], str)
                    or not _SHA.fullmatch(row["input_sha256"])
                    or row["camera_id"] not in value["authorized_cameras"]
                    or row["object_id"] in rows):
                raise ValueError
            rows[row["object_id"]] = row
        # One original crop cannot silently identify two different objects.
        if len({r["crop_id"] for r in rows.values()}) != len(rows):
            raise ValueError
        return value, rows
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise SourceArchiveError("The source archive manifest is invalid") from None


class ObjectCropArchive:
    """Read a frozen export without trusting path names supplied in its JSON.

    ``manifest_sha256`` must be frozen by the migration's admission/quality plan,
    not guessed from a case name. Content hashes bind bytes; they do not attest
    that the operator's supplied native join/permission statements are truthful.
    ``association`` is required for every read and verifies the object/event/
    camera tuple against the current index, rather than reusing row order.
    """

    space = 'clip-basic-v1'

    def __init__(self, root: Path, *, manifest_sha256: str, authorized_cameras: frozenset[str],
                 association):
        self._directory = None
        self._images = None
        try:
            if (not isinstance(manifest_sha256, str) or not _SHA.fullmatch(manifest_sha256)
                    or not callable(association)
                    or not isinstance(authorized_cameras, frozenset) or not authorized_cameras
                    or any(not isinstance(x, str) or not _ID.fullmatch(x)
                           for x in authorized_cameras)):
                raise SourceArchiveError("A frozen manifest and index association reader are required")
            self._directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            _private(os.fstat(self._directory), directory=True)
            raw = _read(self._directory, "manifest.json", MAX_MANIFEST)
            if hashlib.sha256(raw).hexdigest() != manifest_sha256:
                raise SourceArchiveError("The source manifest differs from the admitted manifest")
            self.manifest_sha256 = manifest_sha256
            self._manifest_raw = raw
            self._manifest, self._rows = _manifest(raw)
            if not set(self._manifest["authorized_cameras"]) <= authorized_cameras:
                raise SourceArchiveError("The source archive exceeds current camera authorization")
            self._association = association
            self._images = os.open("images", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=self._directory)
            _private(os.fstat(self._images), directory=True)
        except (OSError, SourceArchiveError) as exc:
            self.close()
            if isinstance(exc, SourceArchiveError):
                raise
            raise SourceArchiveError("The private source archive is unavailable") from None

    def close(self):
        for name in ("_images", "_directory"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def validate_binding(self):
        if self._directory is None or self._images is None:
            raise SourceArchiveError("The source archive is closed")
        if _read(self._directory, "manifest.json", MAX_MANIFEST) != self._manifest_raw:
            raise SourceArchiveError("The source archive manifest changed")
        _private(os.fstat(self._directory), directory=True)
        _private(os.fstat(self._images), directory=True)
        return self.manifest_sha256

    async def __call__(self, object_id):
        try:
            self.validate_binding()
            row = self._rows.get(object_id)
            if row is None:
                return None  # Explicit uncovered history, never a substitute crop.
            actual = self._association(object_id)
            if actual != (row["event_id"], row["camera_id"]):
                raise SourceArchiveError("The source object association differs from the current index")
            raw = _read(self._images, row["input_sha256"] + ".jpg", MAX_IMAGE)
            if hashlib.sha256(raw).hexdigest() != row["input_sha256"]:
                raise SourceArchiveError("An original object crop changed")
            with Image.open(BytesIO(raw)) as image:
                if (image.format != "JPEG" or not 0 < image.width <= 4096
                        or not 0 < image.height <= 4096 or image.width * image.height > 16_777_216):
                    raise SourceArchiveError("An original object crop is not a bounded JPEG")
                image.load()
            return raw
        except SourceArchiveError:
            raise
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise SourceArchiveError("An original object crop is unavailable or unreadable") from None

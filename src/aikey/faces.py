"""Private face identities for the AI Key's local face recognition (#20).

Templates are SFace embeddings kept in ``<state>/faces/identities.json``
(directory 0700, file 0600). They never leave the machine, are never logged,
and every identity can be deleted, or all of them purged. Matching uses
cosine similarity with OpenCV's recommended SFace threshold; a face below
it stays unknown rather than being given the nearest name.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


MATCH_THRESHOLD = 0.363          # OpenCV SFace cosine threshold
_DIMENSION = 128
_MAX_IDENTITIES = 50
_MAX_TEMPLATES = 20
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,64}\Z")


class FaceStoreError(ValueError):
    """Fixed failure without names, images or embeddings."""


def _unit(embedding) -> list[float]:
    if (not isinstance(embedding, list) or len(embedding) != _DIMENSION
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in embedding)):
        raise FaceStoreError("invalid_embedding")
    norm = math.sqrt(sum(v * v for v in embedding))
    if norm == 0:
        raise FaceStoreError("invalid_embedding")
    return [v / norm for v in embedding]


class FaceStore:
    def __init__(self, state_dir: Path):
        self.directory = Path(state_dir) / "faces"
        self.path = self.directory / "identities.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": 1, "identities": {}}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise FaceStoreError("face_store_not_private")
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise FaceStoreError("face_store_invalid")
        return data

    def _save(self, data: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd, temporary = tempfile.mkstemp(prefix=".faces-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def names(self) -> list[str]:
        return sorted(self._load()["identities"])

    def enroll(self, name: str, embedding) -> int:
        if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
            raise FaceStoreError("invalid_name")
        name = name.strip()
        data = self._load()
        identities = data["identities"]
        if name not in identities and len(identities) >= _MAX_IDENTITIES:
            raise FaceStoreError("too_many_identities")
        entry = identities.setdefault(name, {"templates": [], "created": int(time.time())})
        if len(entry["templates"]) >= _MAX_TEMPLATES:
            raise FaceStoreError("too_many_templates")
        entry["templates"].append(_unit(embedding))
        self._save(data)
        return len(entry["templates"])

    def delete(self, name: str) -> bool:
        data = self._load()
        removed = data["identities"].pop(name, None) is not None
        if removed:
            self._save(data)
        return removed

    def purge(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def match(self, embedding) -> tuple[str | None, list[tuple[str, float]]]:
        """Best identity at or above the threshold, plus up to three candidates."""
        probe = _unit(embedding)
        scores = []
        for name, entry in self._load()["identities"].items():
            best = max((sum(a * b for a, b in zip(probe, template))
                        for template in entry["templates"]), default=-1.0)
            if best >= MATCH_THRESHOLD:
                scores.append((name, round(best, 4)))
        scores.sort(key=lambda item: -item[1])
        return (scores[0][0] if scores else None), scores[:3]


def _private_server(url: str) -> str:
    import ipaddress
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise FaceStoreError("face_server_must_be_local") from exc
    if parsed.scheme != "http" or not (address.is_loopback or address.is_private):
        raise FaceStoreError("face_server_must_be_local")
    return url.rstrip("/") + "/v1/faces"


def embed_single_face(image: bytes, server: str) -> list[float]:
    """Exactly one face from an owner-supplied enrollment photo."""
    import urllib.request
    import uuid
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"enroll.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
    body += image + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(_private_server(server), data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        faces = json.load(response)["faces"]
    if len(faces) != 1:
        raise FaceStoreError("enrollment_photo_needs_exactly_one_face")
    return faces[0]["embedding"]


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
            count = store.enroll(args.name, embed_single_face(args.image.read_bytes(), args.server))
            print(json.dumps({"templates": count}))
        elif args.command == "delete":
            print(json.dumps({"deleted": store.delete(args.name or "")}))
        else:
            store.purge()
            print(json.dumps({"purged": True}))
    except (FaceStoreError, OSError, ValueError, KeyError) as exc:
        print(f"local-aikey-faces: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

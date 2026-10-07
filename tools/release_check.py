"""Reproducible, credentials-free release artifacts (#16).

``verify DIST`` normalizes the sdist (sorted members, SOURCE_DATE_EPOCH times,
anonymous owner, normalized modes, a gzip header without a time or name),
refuses artifacts that leak the build environment or private material, and
writes a manifest of every artifact and member digest. ``compare A B`` fails
unless two manifests are identical, so two isolated builds of one commit must
produce byte-identical artifacts.

It never signs, tags, publishes or uploads anything.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import zipfile

MANIFEST_SCHEMA = "aikey-release-manifest/1"
_KEY_BLOCK = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
# Private runtime state never belongs in a release, whatever the build machine.
_PRIVATE_MEMBER = re.compile(r"(^|/)(state|secrets)/|(^|/)[^/]+\.(key|p12|pfx)$|(^|/)\.env$")


class ReleaseError(ValueError):
    """The artifacts are not a clean, reproducible release."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_sdist(path: Path, epoch: int) -> None:
    """Rewrite a .tar.gz sdist so identical content gives identical bytes."""
    with tarfile.open(path, "r:gz") as source:
        members = sorted(source.getmembers(), key=lambda m: m.name)
        payload = [(m, source.extractfile(m).read() if m.isfile() else None) for m in members]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as target:
        for member, data in payload:
            info = tarfile.TarInfo(member.name)
            info.type = member.type
            info.mtime = epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
            info.linkname = member.linkname
            if data is not None:
                info.size = len(data)
                target.addfile(info, io.BytesIO(data))
            else:
                target.addfile(info)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9) as stream:
        stream.write(buffer.getvalue())
    path.write_bytes(compressed.getvalue())


def _members(path: Path) -> list[tuple[str, bytes | None, dict]]:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return [(m.name, archive.extractfile(m).read() if m.isfile() else None,
                     {"uid": m.uid, "uname": m.uname, "gname": m.gname}) for m in archive.getmembers()]
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [(name, archive.read(name), {}) for name in sorted(archive.namelist())]
    raise ReleaseError(f"unexpected artifact type: {path.name}")


def leaks(path: Path, forbidden: list[str]) -> list[str]:
    """Reason codes for build-environment or private material in one artifact."""
    needles = [value.encode() for value in forbidden if value and len(value) >= 3]
    found = set()
    for name, data, owner in _members(path):
        if _PRIVATE_MEMBER.search(name):
            found.add("private_member")
        if owner.get("uid") or owner.get("uname") or owner.get("gname"):
            found.add("builder_account_in_archive")
        blob = name.encode() + b"\0" + (data or b"")
        if any(needle in blob for needle in needles):
            found.add("build_environment_path_or_account")
        if data and _KEY_BLOCK.search(data):
            found.add("private_key_block")
    return sorted(found)


def manifest(dist: Path) -> dict:
    artifacts = sorted(p for p in Path(dist).iterdir() if p.name.endswith((".tar.gz", ".whl")))
    if not artifacts:
        raise ReleaseError("no artifacts to verify")
    return {"schema": MANIFEST_SCHEMA,
            "artifacts": {p.name: _sha(p.read_bytes()) for p in artifacts},
            "files": {p.name: {name: _sha(data) for name, data, _ in _members(p) if data is not None}
                      for p in artifacts}}


def verify(dist: Path, *, epoch: int, forbidden: list[str]) -> dict:
    dist = Path(dist)
    for sdist in dist.glob("*.tar.gz"):
        normalize_sdist(sdist, epoch)
    problems = {p.name: codes for p in sorted(dist.iterdir())
                if p.name.endswith((".tar.gz", ".whl")) and (codes := leaks(p, forbidden))}
    if problems:
        raise ReleaseError(json.dumps(problems, sort_keys=True))
    return manifest(dist)


def compare(first: dict, second: dict) -> list[str]:
    """Differences between two manifests; empty when reproducible."""
    differences = []
    for name in sorted(set(first["artifacts"]) | set(second["artifacts"])):
        if first["artifacts"].get(name) != second["artifacts"].get(name):
            differences.append(f"artifact:{name}")
            a, b = first["files"].get(name, {}), second["files"].get(name, {})
            differences += [f"member:{name}:{member}" for member in sorted(set(a) | set(b))
                            if a.get(member) != b.get(member)]
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify")
    check.add_argument("dist", type=Path)
    check.add_argument("--epoch", type=int, required=True)
    check.add_argument("--forbid", action="append", default=[],
                       help="a build-environment string that must not appear (home, account, checkout path)")
    check.add_argument("--manifest-out", type=Path, required=True)
    diff = sub.add_parser("compare")
    diff.add_argument("first", type=Path)
    diff.add_argument("second", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify(args.dist, epoch=args.epoch, forbidden=args.forbid)
            args.manifest_out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
            print(json.dumps(result["artifacts"], sort_keys=True))
            return 0
        differences = compare(json.loads(args.first.read_text()), json.loads(args.second.read_text()))
        print(json.dumps({"reproducible": not differences, "differences": differences[:50]}))
        return 0 if not differences else 1
    except (ReleaseError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

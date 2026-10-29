"""Check public artifacts and recorded release prerequisites without publishing.

Audit-only mode checks structure and prohibited material. A passing audit is
not release permission. Default mode also requires human evidence records and
an active license. Neither mode establishes copyright ownership or legality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import os
import subprocess
import tarfile
import zipfile

REQUIRED = frozenset({
    "code_origin", "firmware_authorization", "interoperability_scope",
    "contributor_rights", "development_statement", "license_selection",
    "aiport_fresh_adoption", "dependency_licenses", "image_licenses",
    "model_licenses", "history_review", "legal_review",
})
PRIVATE_DIRS = {"state", "secrets", "private", "research", ".git", ".venv"}
FORBIDDEN_SUFFIXES = {".deb", ".bin", ".elf", ".key", ".pem", ".p12", ".pfx",
                      ".pth", ".pt", ".onnx", ".safetensors", ".gguf",
                      ".jpg", ".jpeg", ".png", ".wav", ".mp4", ".ubv",
                      ".tar", ".tgz", ".gz", ".xz", ".zip", ".7z",
                      ".so", ".dylib", ".dll", ".exe", ".tflite", ".pb",
                      ".h5", ".ckpt", ".gif", ".webp", ".mov", ".mp3", ".mkv"}
PRIVATE_KEY = re.compile(rb"(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")


def member_issues(name: str, data: bytes = b"", *, regular: bool = True) -> set[str]:
    path = PurePosixPath(name)
    found = set()
    if (path.is_absolute() or ".." in path.parts or "\\" in name
            or any(part in PRIVATE_DIRS for part in path.parts)
            or any(part == ".env" or part.startswith(".env.") for part in path.parts)
            or path.suffix.lower() in FORBIDDEN_SUFFIXES):
        found.add("prohibited_member")
    if not regular:
        found.add("nonregular_member")
    if PRIVATE_KEY.search(data):
        found.add("private_key")
    if data.startswith((b"\x7fELF", b"MZ", b"\x89PNG", b"\xff\xd8\xff", b"!<arch>", b"\x1f\x8b", b"PK\x03\x04")):
        found.add("binary_payload")
    return found


def artifact_issues(path: Path) -> set[str]:
    found = set()
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                mode = item.external_attr >> 16
                found |= member_issues(item.filename, archive.read(item),
                                       regular=(mode & 0o170000) in (0, 0o100000))
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for item in archive.getmembers():
                if item.isdir():
                    found |= member_issues(item.name)
                    continue
                data = archive.extractfile(item).read() if item.isfile() else b""
                found |= member_issues(item.name, data, regular=item.isfile())
    else:
        found.add("unexpected_artifact")
    return found


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True,
                            stdout=subprocess.PIPE)
    return sorted(p for p in result.stdout.decode().split("\0") if p)


def source_snapshot(root: Path, paths: list[str]) -> str:
    """Any tracked source/dependency/policy change invalidates evidence approval.

    The evidence record itself is excluded so approval has no self-reference.
    This is an input fingerprint, not a legal or authorship attestation.
    """
    digest = hashlib.sha256()
    for name in paths:
        if name == "docs/legal/release-evidence.json":
            continue
        path = root / name
        payload = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        digest.update(name.encode() + b"\0" + hashlib.sha256(payload).digest())
    return digest.hexdigest()


def evidence_issues(record: dict, snapshot: str, *, audit_only: bool) -> set[str]:
    if not isinstance(record, dict) or record.get("schema") != "community-release-evidence/1":
        return {"invalid_evidence_schema"}
    checks = record.get("checks")
    if not isinstance(checks, dict) or set(checks) != REQUIRED:
        return {"invalid_evidence_checks"}
    found = set()
    for key, item in checks.items():
        if not isinstance(item, dict) or item.get("status") not in {"needs_evidence", "verified"}:
            found.add("invalid_evidence_status")
            continue
        if item["status"] == "verified":
            if not all(isinstance(item.get(field), str) and item[field].strip()
                       for field in ("reviewer", "evidence_ref")):
                found.add("unsubstantiated_verification")
        elif not audit_only:
            found.add("needs_evidence:" + key)
    if not audit_only and record.get("reviewed_snapshot_sha256") != snapshot:
        found.add("unreviewed_source_snapshot")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        paths = tracked_paths(root)
        snapshot = source_snapshot(root, paths)
        found = set()
        for name in paths:
            path = root / name
            found |= member_issues(name, b"" if path.is_symlink() else path.read_bytes(),
                                   regular=not path.is_symlink())
        record = json.loads((root / "docs/legal/release-evidence.json").read_text())
        found |= evidence_issues(record, snapshot, audit_only=args.audit_only)
        if not args.audit_only:
            license_path = root / "LICENSE"
            if not license_path.is_file():
                found.add("no_active_project_license")
            elif not license_path.read_text().strip():
                found.add("empty_project_license")
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                   check=True, stdout=subprocess.PIPE).stdout
            if dirty:
                found.add("uncommitted_release_inputs")
        if args.dist is not None:
            artifacts = list(args.dist.iterdir())
            if not artifacts:
                found.add("no_artifacts")
            for artifact in artifacts:
                found |= artifact_issues(artifact)
        print(json.dumps({"mode": "audit-only" if args.audit_only else "release-readiness",
                          "source_snapshot_sha256": snapshot, "issues": sorted(found),
                          "release_approved": False if args.audit_only else not found}))
        return 2 if found else 0
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError,
            zipfile.BadZipFile) as exc:
        # Do not expose raw data, filenames, credentials or subprocess output.
        print(json.dumps({"issues": ["audit_error"], "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

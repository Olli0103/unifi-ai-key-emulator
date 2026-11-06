"""Owner-dispatched release reservation/upload/final read-back, not a signer.

All real entry points obtain a fresh preparation from the paired trusted gate.
They never delete a release, clobber assets, create a tag intentionally, retry
an uncertain write, or manufacture the maintainer's evidence.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import configparser
import io
from email.parser import BytesParser
import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile
import tomllib
import zipfile

_SPEC = importlib.util.spec_from_file_location(
    "paired_release_prepare", Path(__file__).with_name("release_prepare.py")
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


class PublicationError(ValueError):
    pass


def allowed_assets(plan):
    required = {
        "local_aikey-" + plan["version"] + "-py3-none-any.whl",
        "local_aikey-" + plan["version"] + ".tar.gz",
        "SHA256SUMS",
        "package.cdx.json",
        "package-provenance.sigstore.json",
        "package-sbom.sigstore.json",
    }
    for profile in ("aikey", "aiport"):
        required.add(profile + "-index-provenance.sigstore.json")
        for arch in ("amd64", "arm64"):
            base = profile + "-" + arch
            required.update(
                {
                    base + ".cdx.json",
                    base + "-provenance.sigstore.json",
                    base + "-sbom.sigstore.json",
                }
            )
    return required


@contextlib.contextmanager
def snapshot_assets(directory, root, plan):
    """Use private captured bytes for verification, upload and provider read-back."""
    directory = Path(directory).absolute()
    require(directory.resolve() == directory and directory.is_dir(), "release_assets_root_refused")
    paths = list(directory.iterdir())
    required = allowed_assets(plan) | {"release.json", "release-provenance.sigstore.json"}
    names = {path.name for path in paths}
    require(
        required <= names <= required | {"THIRD_PARTY_NOTICES.txt", "CORRESPONDING_SOURCE.json"}
        and all(not path.is_symlink() for path in paths),
        "release_asset_set_mismatch",
    )
    with tempfile.TemporaryDirectory(prefix="release-assets-", dir=Path(root).parent) as target:
        stage = Path(target)
        total = 0
        for path in paths:
            raw = gate.read(path, 64 * 1024 * 1024)
            total += len(raw)
            require(total <= 256 * 1024 * 1024, "release_assets_total_limit")
            (stage / path.name).write_bytes(raw)
        yield stage


def require(condition, code):
    if not condition:
        raise PublicationError(code)


def rows(raw):
    result = gate.load('{"rows":' + raw + "}")["rows"]
    require(isinstance(result, list), "release_metadata_list_refused")
    return result


def fresh(root, tag):
    return gate.prepare(root, tag, publish=True)


def marker(plan):
    return (
        "<!-- local-aikey-release run="
        + plan["workflow_run_id"]
        + " source="
        + plan["source_revision"]
        + " -->"
    )


def releases(root, run=gate.command):
    # gh owns Link pagination. A failed, oversized or incomplete query is not
    # absence; no stderr/message matching converts transport errors into 404.
    pages = rows(
        run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                "repos/" + gate.REPOSITORY + "/releases?per_page=100",
            ],
            cwd=root,
        )
    )
    require(pages and all(isinstance(page, list) for page in pages), "release_listing_incomplete")
    result = [row for page in pages for row in page]
    require(
        len(result) <= 256
        and all(
            isinstance(row, dict)
            and type(row.get("id")) is int
            and row["id"] > 0
            and isinstance(row.get("tag_name"), str)
            for row in result
        ),
        "release_listing_refused",
    )
    require(len({row["id"] for row in result}) == len(result), "release_listing_ambiguous")
    return result


def original(root, plan, *, run=gate.command, published=False, after_create=False):
    found = [row for row in releases(root, run) if row["tag_name"] == plan["tag"]]
    require(len(found) == 1, "release_original_reservation_unavailable")
    value = found[0]
    require(
        type(value.get("draft")) is bool
        and value["draft"] is (not published)
        and isinstance(value.get("body"), str)
        and value["body"].splitlines()
        and value["body"].splitlines()[0] == marker(plan)
        and value["body"].count("<!-- local-aikey-release run=") == 1,
        "release_reservation_owned_elsewhere",
    )
    # target_commitish is not proof of a tag's resolved commit (GitHub can
    # retain a branch name for a pre-existing tag). Fresh preparation checks
    # the actual public tag; later jobs additionally bind the reserved ID.
    if not after_create:
        require(
            type(plan.get("reservation_id")) is int
            and plan["reservation_id"] > 0
            and value["id"] == plan["reservation_id"],
            "release_reservation_id_mismatch",
        )
    return value


def mutate(argv, root):
    """Only explicit prepared GitHub operations, no shell or token arguments."""
    require(argv[:2] == ["gh", "release"], "release_mutation_refused")
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicationError("release_write_outcome_uncertain") from None
    require(completed.returncode == 0, "release_write_outcome_uncertain")


def reserve(root, plan, *, run=gate.command, write=mutate):
    require(
        not any(row["tag_name"] == plan["tag"] for row in releases(root, run)),
        "release_already_exists_needs_reconciliation",
    )
    notes = (
        marker(plan) + "\n\nExperimental release preparation in progress. "
        "Not a completed release or native feature-parity claim.\n"
    )
    acknowledged = True
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(root).parent) as body:
        body.write(notes)
        body.flush()
        try:
            write(
                [
                    "gh",
                    "release",
                    "create",
                    plan["tag"],
                    "--repo",
                    gate.REPOSITORY,
                    "--verify-tag",
                    "--target",
                    plan["source_revision"],
                    "--draft",
                    "--title",
                    "Local AI processor " + plan["version"],
                    "--notes-file",
                    body.name,
                ],
                root,
            )
        except PublicationError:
            acknowledged = False  # Reconcile, never repeat the create request.
    try:
        observed = original(root, plan, run=run, after_create=True)
    except (ValueError, OSError, TypeError, KeyError):
        if not acknowledged:
            raise PublicationError("release_write_outcome_uncertain") from None
        raise
    return {
        "schema": "aikey-release-publication/1",
        "phase": "draft_reserved",
        "release_id": observed["id"],
        "source_revision": plan["source_revision"],
        "tag": plan["tag"],
        "workflow_run_id": plan["workflow_run_id"],
        "write_acknowledged": acknowledged,
        "provider_readback_verified": True,
        "published": False,
    }


def checked_assets(directory, plan):
    """Only the final flat, signed release set; never archives of VM state."""
    directory = Path(directory).absolute()
    require(directory.resolve() == directory and directory.is_dir(), "release_assets_root_refused")
    manifest = gate.load(gate.read(directory / "release.json", 128 * 1024))
    fields = {
        "schema",
        "source_revision",
        "source_snapshot_sha256",
        "tag",
        "version",
        "workflow_run_id",
        "files",
        "images",
    }
    require(
        set(manifest) == fields
        and manifest["schema"] == "aikey-release-assets/1"
        and all(
            manifest[key] == plan[key]
            for key in (
                "source_revision",
                "source_snapshot_sha256",
                "tag",
                "version",
                "workflow_run_id",
            )
        ),
        "release_assets_source_mismatch",
    )
    files = manifest["files"]
    require(isinstance(files, dict) and 6 <= len(files) <= 40, "release_asset_manifest_refused")
    require(
        all(
            isinstance(name, str)
            and re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.-]{0,150}", name)
            and isinstance(info, dict)
            and set(info) == {"sha256", "bytes"}
            and isinstance(info["sha256"], str)
            and gate.HASH.fullmatch(info["sha256"])
            and type(info["bytes"]) is int
            and 0 < info["bytes"] <= 64 * 1024 * 1024
            for name, info in files.items()
        ),
        "release_asset_manifest_refused",
    )
    require(
        not {"release.json", "release-provenance.sigstore.json"} & set(files)
        and {p.name for p in directory.iterdir()}
        == {*files, "release.json", "release-provenance.sigstore.json"},
        "release_asset_set_mismatch",
    )
    required = {
        "local_aikey-" + plan["version"] + "-py3-none-any.whl",
        "local_aikey-" + plan["version"] + ".tar.gz",
        "SHA256SUMS",
        "package.cdx.json",
        "package-provenance.sigstore.json",
        "package-sbom.sigstore.json",
    }
    for profile in ("aikey", "aiport"):
        required.add(profile + "-index-provenance.sigstore.json")
        for arch in ("amd64", "arm64"):
            base = profile + "-" + arch
            required.update(
                {
                    base + ".cdx.json",
                    base + "-provenance.sigstore.json",
                    base + "-sbom.sigstore.json",
                }
            )
    require(
        required
        <= set(files)
        <= required | {"THIRD_PARTY_NOTICES.txt", "CORRESPONDING_SOURCE.json"},
        "release_required_asset_set_mismatch",
    )
    gate.read(directory / "release-provenance.sigstore.json", 16 * 1024 * 1024)
    for name, info in files.items():
        raw = gate.read(directory / name, 64 * 1024 * 1024)
        require(
            len(raw) == info["bytes"] and hashlib.sha256(raw).hexdigest() == info["sha256"],
            "release_asset_bytes_mismatch",
        )
    checksums = "".join(
        info["sha256"] + "  " + name + "\n"
        for name, info in sorted(files.items())
        if name != "SHA256SUMS"
    )
    require(
        gate.read(directory / "SHA256SUMS", 16384) == checksums.encode(),
        "release_checksums_disagree",
    )
    images = manifest["images"]
    require(
        isinstance(images, dict) and set(images) == {"aikey", "aiport"},
        "release_image_manifest_refused",
    )
    for profile, image in images.items():
        require(
            isinstance(image, dict)
            and set(image) == {"digest", "provenance", "architectures"}
            and image["provenance"] == profile + "-index-provenance.sigstore.json"
            and image["provenance"] in files
            and isinstance(image["digest"], str)
            and re.fullmatch("sha256:[0-9a-f]{64}", image["digest"])
            and isinstance(image["architectures"], dict)
            and set(image["architectures"]) == {"amd64", "arm64"},
            "release_image_manifest_refused",
        )
        for architecture, descriptor in image["architectures"].items():
            base = profile + "-" + architecture
            require(
                isinstance(descriptor, dict)
                and set(descriptor)
                == {"digest", "configuration_digest", "sbom", "provenance", "sbom_attestation"}
                and all(
                    isinstance(descriptor[key], str)
                    and re.fullmatch("sha256:[0-9a-f]{64}", descriptor[key])
                    for key in ("digest", "configuration_digest")
                )
                and descriptor["sbom"] == base + ".cdx.json"
                and descriptor["provenance"] == base + "-provenance.sigstore.json"
                and descriptor["sbom_attestation"] == base + "-sbom.sigstore.json"
                and all(
                    descriptor[key] in files for key in ("sbom", "provenance", "sbom_attestation")
                ),
                "release_image_manifest_refused",
            )
            bom = gate.load(gate.read(directory / descriptor["sbom"], 16 * 1024 * 1024))
            subject = bom.get("metadata", {}).get("component", {})
            require(
                bom.get("bomFormat") == "CycloneDX"
                and subject.get("name") == "sha256"
                and "sha256:" + subject.get("version", "") == descriptor["configuration_digest"],
                "release_sbom_configuration_mismatch",
            )
    return manifest


def docker_read(reference, root):
    """Read only a known immutable own image; credentials remain in Docker's client."""
    require(
        isinstance(reference, str)
        and re.fullmatch(
            r"ghcr\.io/olli0103/[a-z0-9][a-z0-9._-]{0,100}@sha256:[0-9a-f]{64}", reference
        ),
        "release_image_reference_refused",
    )
    with tempfile.TemporaryFile(dir=Path(root).parent) as output:
        try:
            result = subprocess.run(
                ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PublicationError("release_image_observation_unavailable") from None
        require(
            result.returncode == 0 and 0 < output.tell() <= 128 * 1024,
            "release_image_observation_unavailable",
        )
        output.seek(0)
        raw = output.read()
    require(
        "sha256:" + hashlib.sha256(raw).hexdigest() == reference.rsplit("@", 1)[1],
        "release_registry_manifest_digest_mismatch",
    )
    return gate.load(raw)


def verify_packages(root, plan, directory, *, run=gate.command):
    """Audit actual distribution bytes and bind every shipped module/license."""
    root, directory = Path(root), Path(directory)
    source = run(["git", "ls-files", "-z"], cwd=root).split("\0")
    tracked = {name for name in source if name}
    modules = {
        name.removeprefix("src/"): name
        for name in tracked
        if name.startswith("src/aikey/") and name.endswith(".py")
    }
    require(modules, "release_package_source_modules_missing")
    declared_licenses = tomllib.loads(gate.read(root / "pyproject.toml").decode())["project"].get("license-files")
    require(
        isinstance(declared_licenses, list) and "LICENSE" in declared_licenses
        and 1 <= len(declared_licenses) <= 32
        and all(isinstance(name, str) and re.fullmatch(r"[A-Z][A-Za-z0-9_.-]{0,127}", name)
                and name in tracked for name in declared_licenses)
        and len(set(declared_licenses)) == len(declared_licenses),
        "release_package_declared_licenses_refused",
    )
    wheel = directory / ("local_aikey-" + plan["version"] + "-py3-none-any.whl")
    sdist = directory / ("local_aikey-" + plan["version"] + ".tar.gz")
    try:
        with zipfile.ZipFile(wheel) as package:
            entries = package.infolist()
            require(
                len(entries) <= 10000
                and sum(item.file_size for item in entries) <= 64 * 1024 * 1024
                and all(item.file_size <= 8 * 1024 * 1024 for item in entries),
                "release_wheel_size_limit",
            )

        class Limited:
            def __init__(self, source):
                self.source, self.remaining = source, 64 * 1024 * 1024

            def read(self, size=-1):
                size = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
                raw = self.source.read(size)
                self.remaining -= len(raw)
                require(self.remaining >= 0, "release_sdist_stream_limit")
                return raw

        with (
            gzip.open(sdist, "rb") as stream,
            tarfile.open(fileobj=Limited(stream), mode="r|") as package,
        ):
            count = total = 0
            for item in package:
                count += 1
                total += item.size
                require(
                    count <= 10000 and total <= 64 * 1024 * 1024 and item.size <= 8 * 1024 * 1024,
                    "release_sdist_size_limit",
                )
                require(item.isfile() or item.isdir(), "release_sdist_member_refused")
        spec = importlib.util.spec_from_file_location(
            "paired_community", Path(__file__).with_name("community_release_check.py")
        )
        community = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(community)
        with tempfile.TemporaryDirectory(prefix="release-audit-", dir=root.parent) as temporary:
            stage = Path(temporary)
            for path in (wheel, sdist):
                (stage / path.name).write_bytes(gate.read(path, 64 * 1024 * 1024))
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                status = community.main(["--root", str(root), "--dist", str(stage)])
            report = gate.load(capture.getvalue())
            require(
                status == 0
                and report.get("issues") == []
                and report.get("release_approved") is True
                and report.get("source_snapshot_sha256") == plan["source_snapshot_sha256"],
                "release_distribution_non_audit_gate_blocked",
            )
        with zipfile.ZipFile(wheel) as package:
            infos = package.infolist()
            require(
                len(infos) <= 10000
                and sum(item.file_size for item in infos) <= 64 * 1024 * 1024
                and all(item.file_size <= 8 * 1024 * 1024 for item in infos),
                "release_wheel_size_limit",
            )
            names = package.namelist()
            metadata_root = "local_aikey-" + plan["version"] + ".dist-info/"
            generated = {
                metadata_root + name
                for name in (
                    "METADATA",
                    "WHEEL",
                    "RECORD",
                    "entry_points.txt",
                    "top_level.txt",
                )
            } | {metadata_root + "licenses/" + name for name in declared_licenses}
            require(
                len(names) == len(set(names))
                and set(names) <= set(modules) | generated
                and {name for name in names if name.startswith("aikey/") and name.endswith(".py")}
                == set(modules),
                "release_wheel_module_set_mismatch",
            )
            for name, source_name in modules.items():
                require(
                    package.read(name)
                    == gate.read(root / source_name, 2 * 1024 * 1024, allow_empty=True),
                    "release_wheel_source_bytes_mismatch",
                )
            require(
                all(metadata_root + "licenses/" + name in names
                    and package.read(metadata_root + "licenses/" + name)
                    == gate.read(root / name, 2 * 1024 * 1024)
                    for name in declared_licenses),
                "release_wheel_license_mismatch",
            )
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            require(len(metadata) == 1, "release_wheel_metadata_mismatch")
            parsed = BytesParser().parsebytes(package.read(metadata[0]))
            require(
                parsed.get_all("Name") == ["local-aikey"]
                and parsed.get_all("Version") == [plan["version"]],
                "release_wheel_metadata_mismatch",
            )
            wheel_info = BytesParser().parsebytes(package.read(metadata_root + "WHEEL"))
            require(
                wheel_info.get_all("Wheel-Version") == ["1.0"]
                and wheel_info.get_all("Root-Is-Purelib") == ["true"]
                and wheel_info.get_all("Tag") == ["py3-none-any"],
                "release_wheel_platform_refused",
            )
            scripts = tomllib.loads(gate.read(root / "pyproject.toml").decode())["project"].get(
                "scripts", {}
            )
            entry = metadata_root + "entry_points.txt"
            if scripts or entry in names:
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                parser.read_string(package.read(entry).decode())
                require(
                    parser.sections() == ["console_scripts"]
                    and dict(parser["console_scripts"]) == scripts,
                    "release_wheel_entry_points_mismatch",
                )
            records = list(csv.reader(io.StringIO(package.read(metadata_root + "RECORD").decode())))
            require(
                len(records) == len(names)
                and all(len(row) == 3 for row in records)
                and {row[0] for row in records} == set(names),
                "release_wheel_record_set_mismatch",
            )
            for name, hash_value, size in records:
                raw = package.read(name)
                require(
                    (
                        hash_value == size == ""
                        if name == metadata_root + "RECORD"
                        else hash_value
                        == "sha256="
                        + base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
                        .decode()
                        .rstrip("=")
                        and size == str(len(raw))
                    ),
                    "release_wheel_record_digest_mismatch",
                )
        prefix = "local_aikey-" + plan["version"] + "/"
        with tarfile.open(sdist, "r:gz") as package:
            members = package.getmembers()
            require(
                len(members) <= 10000
                and len({item.name for item in members}) == len(members)
                and all(
                    item.name.startswith(prefix) or item.name == prefix[:-1] for item in members
                ),
                "release_sdist_member_set_mismatch",
            )
            require(
                sum(item.size for item in members) <= 64 * 1024 * 1024
                and all(
                    item.uid == item.gid == 0
                    and not item.uname
                    and not item.gname
                    and item.mtime == plan["source_date_epoch"]
                    for item in members
                ),
                "release_sdist_not_canonical",
            )
            originals = {}
            generated = {
                "PKG-INFO",
                "setup.cfg",
                *(
                    "src/local_aikey.egg-info/" + name
                    for name in (
                        "PKG-INFO",
                        "SOURCES.txt",
                        "dependency_links.txt",
                        "entry_points.txt",
                        "requires.txt",
                        "top_level.txt",
                    )
                ),
            }
            for item in members:
                if item.isdir():
                    continue
                require(
                    item.isfile() and 0 <= item.size <= 8 * 1024 * 1024,
                    "release_sdist_member_refused",
                )
                name = item.name.removeprefix(prefix)
                raw = package.extractfile(item).read()
                if name in tracked:
                    require(
                        raw == gate.read(root / name, 8 * 1024 * 1024, allow_empty=True),
                        "release_sdist_source_bytes_mismatch",
                    )
                    originals[name] = raw
                else:
                    require(name in generated, "release_sdist_untracked_payload")
                    if name == "setup.cfg":
                        require(
                            raw == b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
                            "release_sdist_generated_config_refused",
                        )
            require(
                {*declared_licenses, "pyproject.toml", *modules.values()} <= set(originals),
                "release_sdist_source_modules_missing",
            )
        spec = importlib.util.spec_from_file_location(
            "paired_artifact_check", Path(__file__).with_name("release_check.py")
        )
        hygiene = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hygiene)
        forbidden = [os.environ.get("HOME", ""), str(root), str(directory)]
        require(
            not hygiene.leaks(wheel, forbidden) and not hygiene.leaks(sdist, forbidden),
            "release_distribution_environment_leak",
        )
    except (
        zipfile.BadZipFile,
        tarfile.TarError,
        EOFError,
        configparser.Error,
        UnicodeError,
        KeyError,
    ):
        raise PublicationError("release_distribution_archive_refused") from None


def verify_subject(
    root, plan, directory, subject, bundle, *, sbom=False, predicate_path=None, run=gate.command
):
    """One maintained Sigstore verifier with the same policy at every handoff."""
    policy = [
        "--repo",
        gate.REPOSITORY,
        "--source-digest",
        plan["source_revision"],
        "--signer-digest",
        plan["source_revision"],
        "--source-ref",
        "refs/heads/main",
        "--signer-workflow",
        gate.REPOSITORY + "/.github/workflows/publish-release.yml",
        "--cert-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        "--deny-self-hosted-runners",
    ]

    argv = [
        "gh",
        "attestation",
        "verify",
        subject,
        *policy,
        "--bundle",
        str(Path(directory) / bundle),
    ]
    if sbom:
        argv += ["--predicate-type", "https://cyclonedx.org/bom"]
    run(argv, cwd=root)  # Nonzero/unknown status raises; output text is not authority.
    if sbom:
        require(predicate_path is not None, "release_sbom_predicate_file_required")
        # This is NOT cryptographic verification. gh just verified this single
        # standard bundle. Bind its verified predicate to the distributed file.
        value = gate.load(gate.read(Path(directory) / bundle, 16 * 1024 * 1024))
        try:
            statement = gate.load(base64.b64decode(value["dsseEnvelope"]["payload"], validate=True))
        except (KeyError, TypeError, ValueError):
            raise PublicationError("release_sbom_verified_payload_refused") from None
        require(
            statement.get("predicateType") == "https://cyclonedx.org/bom"
            and statement.get("predicate")
            == gate.load(gate.read(predicate_path, 16 * 1024 * 1024)),
            "release_sbom_verified_predicate_disagrees",
        )


def verify_signatures(root, plan, directory, manifest, *, run=gate.command, inspect=docker_read):
    """Use maintained gh/Sigstore verification; never parse a fake SUCCESS string."""
    directory = Path(directory)

    def verify(subject, bundle, *, sbom=False, predicate_path=None):
        verify_subject(
            root,
            plan,
            directory,
            subject,
            bundle,
            sbom=sbom,
            predicate_path=predicate_path,
            run=run,
        )

    verify(str(directory / "release.json"), "release-provenance.sigstore.json")
    verify(str(directory / "SHA256SUMS"), "release-provenance.sigstore.json")
    for name in (
        "local_aikey-" + plan["version"] + "-py3-none-any.whl",
        "local_aikey-" + plan["version"] + ".tar.gz",
    ):
        verify(str(directory / name), "package-provenance.sigstore.json")
        verify(
            str(directory / name),
            "package-sbom.sigstore.json",
            sbom=True,
            predicate_path=directory / "package.cdx.json",
        )
    for profile, image in manifest["images"].items():
        prefix = plan["images"][profile] + "@"
        index = inspect(prefix + image["digest"], root)
        descriptors = index.get("manifests")
        require(
            index.get("schemaVersion") == 2
            and isinstance(descriptors, list)
            and len(descriptors) == 2,
            "release_index_manifest_refused",
        )
        actual = {}
        for descriptor in descriptors:
            require(
                isinstance(descriptor, dict)
                and isinstance(descriptor.get("platform"), dict)
                and descriptor["platform"].get("os") == "linux"
                and descriptor["platform"].get("architecture") in {"amd64", "arm64"}
                and descriptor["platform"]["architecture"] not in actual,
                "release_index_platform_refused",
            )
            actual[descriptor["platform"]["architecture"]] = descriptor.get("digest")
        require(
            actual == {arch: data["digest"] for arch, data in image["architectures"].items()},
            "release_index_architecture_digest_mismatch",
        )
        verify("oci://" + prefix + image["digest"], image["provenance"])
        for data in image["architectures"].values():
            observed = inspect(prefix + data["digest"], root)
            require(
                observed.get("schemaVersion") == 2
                and isinstance(observed.get("config"), dict)
                and observed["config"].get("digest") == data["configuration_digest"],
                "release_image_configuration_mismatch",
            )
            verify("oci://" + prefix + data["digest"], data["provenance"])
            verify(
                "oci://" + prefix + data["digest"],
                data["sbom_attestation"],
                sbom=True,
                predicate_path=directory / data["sbom"],
            )


def public_images(root, plan, *, run=gate.command):
    """First-publish visibility is the maintainer's UI setting, never changed here."""
    for image in plan["images"].values():
        name = image.rsplit("/", 1)[1]
        value = gate.load(
            run(["gh", "api", f"users/{gate.OWNER}/packages/container/{name}"], cwd=root)
        )
        require(
            value.get("name") == name
            and value.get("package_type") == "container"
            and value.get("visibility") == "public"
            and isinstance(value.get("repository"), dict)
            and value["repository"].get("full_name") == gate.REPOSITORY,
            "release_image_visibility_or_repository_unverified",
        )


def asset_rows(root, release_id, *, run=gate.command):
    values = rows(
        run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{gate.REPOSITORY}/releases/{release_id}/assets?per_page=100",
            ],
            cwd=root,
        )
    )
    require(
        values and all(isinstance(page, list) for page in values),
        "release_asset_listing_incomplete",
    )
    assets = [row for page in values for row in page]
    require(all(isinstance(row, dict) for row in assets), "release_asset_listing_refused")
    return assets


def uploaded_assets(root, plan, manifest, directory, *, run=gate.command, published=False):
    release = original(root, plan, run=run, published=published)
    assets = asset_rows(root, release["id"], run=run)
    expected = dict(manifest["files"])
    raw = gate.read(Path(directory) / "release.json", 128 * 1024)
    expected["release.json"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    raw = gate.read(Path(directory) / "release-provenance.sigstore.json", 16 * 1024 * 1024)
    expected["release-provenance.sigstore.json"] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    require(
        len(assets) == len(expected)
        and all(isinstance(row, dict) for row in assets)
        and {row.get("name") for row in assets} == set(expected),
        "release_uploaded_asset_set_mismatch",
    )
    for row in assets:
        item = expected[row["name"]]
        require(
            type(row.get("id")) is int
            and row["id"] > 0
            and row.get("state") == "uploaded"
            and type(row.get("size")) is int
            and row["size"] == item["bytes"]
            and row.get("digest") == "sha256:" + item["sha256"],
            "release_uploaded_asset_digest_unverified",
        )
    return release


def upload(root, plan, directory, *, run=gate.command, write=mutate):
    with snapshot_assets(directory, root, plan) as snapshot:
        return _upload_snapshot(root, plan, snapshot, run=run, write=write)


def _upload_snapshot(root, plan, directory, *, run=gate.command, write=mutate):
    manifest = checked_assets(directory, plan)
    original(root, plan, run=run)
    public_images(root, plan, run=run)
    verify_signatures(root, plan, directory, manifest, run=run)
    verify_packages(root, plan, directory, run=run)
    if asset_rows(root, plan["reservation_id"], run=run):
        observed = uploaded_assets(root, plan, manifest, directory, run=run)
        return {
            "schema": "aikey-release-publication/1",
            "phase": "assets_uploaded",
            "release_id": observed["id"],
            "tag": plan["tag"],
            "published": False,
            "write_acknowledged": False,
            "provider_readback_verified": True,
            "reconciled_existing": True,
        }
    paths = [
        str(Path(directory) / name)
        for name in sorted({*manifest["files"], "release.json", "release-provenance.sigstore.json"})
    ]
    acknowledged = True
    try:
        write(["gh", "release", "upload", plan["tag"], "--repo", gate.REPOSITORY, *paths], root)
    except PublicationError:
        acknowledged = False  # Asset names are create-only; no --clobber or retry.
    try:
        observed = uploaded_assets(root, plan, manifest, directory, run=run)
    except (ValueError, OSError, TypeError, KeyError):
        if not acknowledged:
            raise PublicationError("release_write_outcome_uncertain") from None
        raise
    return {
        "schema": "aikey-release-publication/1",
        "phase": "assets_uploaded",
        "release_id": observed["id"],
        "tag": plan["tag"],
        "write_acknowledged": acknowledged,
        "provider_readback_verified": True,
        "published": False,
    }


def complete(root, plan, directory, *, run=gate.command, write=mutate):
    with snapshot_assets(directory, root, plan) as snapshot:
        return _complete_snapshot(root, plan, snapshot, run=run, write=write)


def _complete_snapshot(root, plan, directory, *, run=gate.command, write=mutate):
    manifest = checked_assets(directory, plan)
    public_images(root, plan, run=run)
    uploaded_assets(root, plan, manifest, directory, run=run)
    verify_signatures(root, plan, directory, manifest, run=run)
    verify_packages(root, plan, directory, run=run)
    notes = (
        marker(plan)
        + "\n\nExperimental Local AI processor "
        + plan["version"]
        + "\n\nPublic source commit: `"
        + plan["source_revision"]
        + "`. Verify signatures and digests before installation; see "
        + "https://github.com/"
        + gate.REPOSITORY
        + "/blob/"
        + plan["source_revision"]
        + "/docs/releasing.md .\n\nThis is not native feature-parity, quality, sustained-operation "
        "or independent-installation acceptance. No model weights or camera data are included.\n"
    )
    acknowledged = True
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(root).parent) as body:
        body.write(notes)
        body.flush()
        try:
            write(
                [
                    "gh",
                    "release",
                    "edit",
                    plan["tag"],
                    "--repo",
                    gate.REPOSITORY,
                    "--draft=false",
                    "--prerelease=" + ("true" if plan["prerelease"] else "false"),
                    "--notes-file",
                    body.name,
                ],
                root,
            )
        except PublicationError:
            acknowledged = False
    try:
        observed = uploaded_assets(root, plan, manifest, directory, run=run, published=True)
    except (ValueError, OSError, TypeError, KeyError):
        if not acknowledged:
            raise PublicationError("release_write_outcome_uncertain") from None
        raise
    require(
        observed.get("prerelease") is plan["prerelease"], "release_publication_state_unverified"
    )
    require(observed.get("body") == notes, "release_publication_notes_unverified")
    return {
        "schema": "aikey-release-publication/1",
        "phase": "published",
        "release_id": observed["id"],
        "tag": plan["tag"],
        "source_revision": plan["source_revision"],
        "workflow_run_id": plan["workflow_run_id"],
        "published": True,
        "write_acknowledged": acknowledged,
        "provider_readback_verified": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("reserve", "upload", "complete"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--release-id", type=int)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        plan = fresh(root, args.tag)
        if args.command == "reserve":
            require(
                args.assets is None and args.release_id is None, "release_assets_option_refused"
            )
            result = reserve(root, plan)
        else:
            require(
                args.assets is not None and type(args.release_id) is int and args.release_id > 0,
                "release_assets_and_reservation_required",
            )
            plan = dict(plan, reservation_id=args.release_id)
            result = (upload if args.command == "upload" else complete)(root, plan, args.assets)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        code = (
            error.args[0]
            if isinstance(error, (PublicationError, gate.PreparationError)) and error.args
            else "release_publication_unavailable"
        )
        print(
            json.dumps(
                {
                    "schema": "aikey-release-publication/1",
                    "error": code,
                    "publication_outcome": "not_asserted",
                    "automatic_retry": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

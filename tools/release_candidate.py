"""Build handoffs for the owner-gated workflow, using Docker/gh, not custom crypto.

Intent files are create-only and retained before registry writes. Attempt tags
include the GitHub run ID; no latest/version-only tag or foreign image is moved.
Unknown outcomes are observed once and never automatically retried or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import container_install_check as installation
import image_sbom_check as inventory
import release_publication as publication

gate = publication.gate
require = publication.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def emit(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    require(gate.read(path, 128 * 1024) == raw, "release_candidate_receipt_uncertain")


def output(values):
    path = os.environ.get("GITHUB_OUTPUT")
    require(path and Path(path).is_file(), "release_workflow_output_required")
    require(
        all(
            re.fullmatch("[a-z_]+", key)
            and isinstance(value, str)
            and "\n" not in value
            and "\r" not in value
            for key, value in values.items()
        ),
        "release_workflow_output_refused",
    )
    with open(path, "a", encoding="utf-8") as target:
        for key, value in values.items():
            target.write(key + "=" + value + "\n")


def docker(argv, root, *, mutate=False, timeout=60):
    require(argv[:1] == ["docker"], "release_docker_command_refused")
    with tempfile.TemporaryFile(dir=Path(root).parent) as capture:
        try:
            result = subprocess.run(
                argv,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if mutate else capture,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise publication.PublicationError(
                "release_registry_write_uncertain"
                if mutate
                else "release_registry_observation_unavailable"
            ) from None
        require(
            result.returncode == 0,
            "release_registry_write_uncertain"
            if mutate
            else "release_registry_observation_unavailable",
        )
        require(capture.tell() <= 128 * 1024, "release_registry_output_limit")
        capture.seek(0)
        return capture.read()


def tagged(plan, profile, architecture=None):
    require(
        profile in ("aikey", "aiport") and architecture in (None, "amd64", "arm64"),
        "release_candidate_profile_refused",
    )
    return (
        plan["images"][profile]
        + ":"
        + plan["version"]
        + ("-" + architecture if architecture else "-index")
        + "-run"
        + plan["workflow_run_id"]
    )


def observed_tag(root, reference, *, run=docker):
    # Called only with references constructed from the freshly approved plan.
    require(
        re.fullmatch(
            r"ghcr\.io/olli0103/[a-z0-9][a-z0-9._-]{0,100}:"
            r"[0-9]+\.[0-9]+\.[0-9]+(?:rc[1-9][0-9]*)?-(?:amd64|arm64|index)-run[1-9][0-9]*",
            reference,
        ),
        "release_candidate_reference_refused",
    )
    raw = run(["docker", "buildx", "imagetools", "inspect", "--raw", reference], root)
    require(0 < len(raw) <= 128 * 1024, "release_registry_output_limit")
    return "sha256:" + digest(raw), gate.load(raw)


def identity(plan, *, architecture=None):
    result = {
        key: plan[key]
        for key in (
            "source_revision",
            "source_snapshot_sha256",
            "tag",
            "version",
            "workflow_run_id",
            "reservation_id",
        )
    }
    if architecture is not None:
        result["architecture"] = architecture
    return result


def image_intent(root, plan, results, architecture, destination, *, qualified=None, run=docker):
    require(architecture in ("amd64", "arm64"), "release_candidate_architecture_refused")
    expected_runner = "X64" if architecture == "amd64" else "ARM64"
    require(os.environ.get("RUNNER_ARCH") == expected_runner, "release_native_runner_mismatch")
    nonce = plan["workflow_run_id"] + "-1-" + architecture
    require(os.environ.get("INSTALL_NONCE") == nonce, "release_native_nonce_mismatch")
    records = installation.checked_records(results, plan["source_revision"], architecture)
    require(len(records) == 8, "release_native_cases_incomplete")
    require(qualified is not None, "release_qualified_image_inventory_required")
    qualified_inventory = gate.load(gate.read(Path(qualified) / "image-inventory.json", 128 * 1024))
    qualified_install = gate.load(gate.read(Path(qualified) / "summary.json", 128 * 1024))
    require(
        qualified_inventory.get("schema") == "aikey-image-inventory-summary/1"
        and qualified_inventory.get("revision") == plan["source_revision"]
        and qualified_inventory.get("architecture") == architecture
        and qualified_inventory.get("cleanup_verified") is True
        and qualified_install.get("revision") == plan["source_revision"]
        and qualified_install.get("architecture") == architecture
        and qualified_install.get("passed") is True
        and qualified_install.get("cases_finished") == 8,
        "release_qualified_image_inventory_mismatch",
    )
    expected = inventory.expected_python(root)
    profiles = {}
    for profile in ("aikey", "aiport"):
        receipt = gate.load(gate.read(Path(results) / (profile + "-inventory.json")))
        raw = gate.read(Path(results) / (profile + "-sbom.cdx.json"), 32 * 1024 * 1024)
        qualified_raw = gate.read(Path(qualified) / (profile + "-sbom.cdx.json"), 16 * 1024 * 1024)
        qualified_profile = qualified_inventory["profiles"][profile]
        require(
            qualified_profile.get("sbom_sha256") == digest(qualified_raw)
            and qualified_profile.get("revision") == plan["source_revision"]
            and qualified_profile.get("architecture") == architecture,
            "release_qualified_image_sbom_changed",
        )
        require(
            license_catalog(gate.load(raw)) == license_catalog(gate.load(qualified_raw)),
            "release_image_catalog_changed_since_qualification",
        )
        require(
            receipt.get("schema") == inventory.RECEIPT
            and receipt.get("revision") == plan["source_revision"]
            and receipt.get("architecture") == architecture
            and receipt.get("profile") == profile
            and receipt.get("nonce") == nonce
            and receipt.get("scanner_version") == inventory.VERSION
            and receipt.get("conversion_verified") is True
            and receipt.get("runtime_lock_packages_verified") == len(expected)
            and receipt.get("sbom_sha256") == digest(raw),
            "release_inventory_handoff_refused",
        )
        local = "local-aikey-install:" + nonce + "-" + profile
        inspected = json.loads(run(["docker", "image", "inspect", local], root))
        subject = inventory.subject(
            inspected, plan["source_revision"], architecture, nonce, profile
        )
        require(
            subject["image_id"] == receipt.get("image_id"), "release_image_changed_since_inventory"
        )
        require(
            inspected[0]["Config"]["Labels"].get("org.opencontainers.image.source")
            == "https://github.com/" + gate.REPOSITORY,
            "release_image_repository_label_missing",
        )
        # Installation entry points already check every module byte. Also bind
        # their module count and installed version to this exact source here.
        count = len(
            [
                name
                for name in gate.command(["git", "ls-files", "-z"], cwd=root).split("\0")
                if name.startswith("src/aikey/") and name.endswith(".py")
            ]
        )
        positives = [
            row["response"]
            for row in records
            if row["profile"] == profile and row["case"] in ("create", "reopen")
        ]
        require(
            all(
                row["module_count"] == count and row["version"] == plan["version"]
                for row in positives
            ),
            "release_installed_source_handoff_refused",
        )
        profiles[profile] = {
            "configuration_digest": subject["image_id"],
            "reference": tagged(plan, profile, architecture),
            "sbom_sha256": digest(raw),
        }
    value = {
        "schema": "aikey-release-image-intent/1",
        **identity(plan, architecture=architecture),
        "profiles": profiles,
    }
    emit(Path(destination), value)
    return value


def license_catalog(value):
    """Raw maintained CycloneDX declarations, not legal clearance or image equality.

    Configuration IDs, layer/location IDs and fresh timestamps are deliberately
    not identities of a package's license/version declaration. Unknown fields
    stay unknown. Syft itself validated the native/CDX conversion at collection.
    """
    require(
        value.get("bomFormat") == "CycloneDX" and isinstance(value.get("components"), list),
        "release_image_catalog_refused",
    )
    fields = (
        "type",
        "name",
        "version",
        "purl",
        "licenses",
        "hashes",
        "externalReferences",
        "cpe",
        "supplier",
        "author",
        "publisher",
        "description",
    )
    rows = []
    for row in value["components"]:
        require(isinstance(row, dict), "release_image_catalog_refused")
        if row.get("type") in ("application", "library", "framework", "operating-system"):
            rows.append(json.dumps({key: row[key] for key in fields if key in row}, sort_keys=True))
    require(rows, "release_image_catalog_empty")
    return sorted(rows)


def read_intent(path, plan, architecture):
    value = gate.load(gate.read(path, 128 * 1024))
    require(
        set(value) == {"schema", *identity(plan, architecture=architecture), "profiles"}
        and value["schema"] == "aikey-release-image-intent/1"
        and all(
            value[key] == item for key, item in identity(plan, architecture=architecture).items()
        )
        and isinstance(value["profiles"], dict)
        and set(value["profiles"]) == {"aikey", "aiport"},
        "release_image_intent_refused",
    )
    for profile, item in value["profiles"].items():
        require(
            isinstance(item, dict)
            and set(item) == {"configuration_digest", "reference", "sbom_sha256"}
            and item["reference"] == tagged(plan, profile, architecture)
            and isinstance(item["configuration_digest"], str)
            and re.fullmatch("sha256:[0-9a-f]{64}", item["configuration_digest"])
            and isinstance(item["sbom_sha256"], str)
            and gate.HASH.fullmatch(item["sbom_sha256"]),
            "release_image_intent_refused",
        )
    return value


def push_images(root, plan, intent_path, architecture, results, destination, *, run=docker):
    intent = read_intent(intent_path, plan, architecture)
    require(not Path(destination).exists(), "release_image_attempt_already_recorded")
    profiles = {}
    for profile, item in intent["profiles"].items():
        raw = gate.read(Path(results) / (profile + "-sbom.cdx.json"), 32 * 1024 * 1024)
        require(digest(raw) == item["sbom_sha256"], "release_image_sbom_changed")
        # Inspect the immutable local ID again immediately before tagging it.
        observed = json.loads(
            run(["docker", "image", "inspect", item["configuration_digest"]], root)
        )
        nonce = plan["workflow_run_id"] + "-1-" + architecture
        subject = inventory.subject(observed, plan["source_revision"], architecture, nonce, profile)
        require(
            subject["image_id"] == item["configuration_digest"],
            "release_local_image_identity_changed",
        )
    emit(
        Path(intent_path).with_suffix(".started.json"),
        {"schema": "aikey-release-registry-started/1", **identity(plan, architecture=architecture)},
    )
    for profile, item in intent["profiles"].items():
        run(
            ["docker", "image", "tag", item["configuration_digest"], item["reference"]],
            root,
            mutate=True,
        )
        acknowledged = True
        try:
            run(["docker", "image", "push", item["reference"]], root, mutate=True, timeout=1200)
        except publication.PublicationError:
            acknowledged = False
        # Positive config-byte identity is required even after an acknowledged
        # push. A lost response is observed once, never retried.
        manifest_digest, manifest = observed_tag(root, item["reference"], run=run)
        require(
            manifest.get("schemaVersion") == 2
            and isinstance(manifest.get("config"), dict)
            and manifest["config"].get("digest") == item["configuration_digest"],
            "release_pushed_configuration_unverified",
        )
        # Remove only our newly added local registry tag. The original owned
        # install tag remains for the existing cleanup/finalization contract.
        run(["docker", "image", "rm", item["reference"]], root, mutate=True)
        profiles[profile] = {**item, "digest": manifest_digest, "write_acknowledged": acknowledged}
    value = {
        "schema": "aikey-release-staged-images/1",
        **identity(plan, architecture=architecture),
        "profiles": profiles,
    }
    emit(Path(destination), value)
    return value


def stage_assets(root, plan, results, staged, architecture, destination):
    # Cleanup must have completed before this metadata is allowed downstream.
    nonce = plan["workflow_run_id"] + "-1-" + architecture
    inventory.finalize(results, plan["source_revision"], architecture, nonce)
    saved = gate.load(gate.read(staged, 128 * 1024))
    require(
        saved.get("schema") == "aikey-release-staged-images/1"
        and all(
            saved.get(key) == value
            for key, value in identity(plan, architecture=architecture).items()
        ),
        "release_staged_images_handoff_refused",
    )
    target = Path(destination)
    target.mkdir(mode=0o700)
    for profile in ("aikey", "aiport"):
        raw = gate.read(Path(results) / (profile + "-sbom.cdx.json"), 32 * 1024 * 1024)
        require(
            digest(raw) == saved["profiles"][profile]["sbom_sha256"], "release_staged_sbom_changed"
        )
        (target / (profile + "-" + architecture + ".cdx.json")).write_bytes(raw)
    emit(target / ("staged-" + architecture + ".json"), saved)
    emit(
        target / ("install-" + architecture + ".json"),
        gate.load(gate.read(Path(results) / "summary.json")),
    )


def indexes(root, plan, inputs, destination, *, run=docker):
    target = Path(destination)
    target.mkdir(mode=0o700)
    architectures = {}
    for architecture in ("amd64", "arm64"):
        folder = Path(inputs) / ("release-images-" + architecture)
        saved = gate.load(gate.read(folder / ("staged-" + architecture + ".json"), 128 * 1024))
        proof = gate.load(gate.read(folder / ("install-" + architecture + ".json")))
        require(
            saved.get("schema") == "aikey-release-staged-images/1"
            and all(
                saved.get(key) == value
                for key, value in identity(plan, architecture=architecture).items()
            )
            and proof.get("schema") == installation.SUMMARY
            and proof.get("passed") is True
            and proof.get("revision") == plan["source_revision"]
            and proof.get("architecture") == architecture
            and proof.get("cases_finished") == 8
            and all(
                proof.get(key) == 0
                for key in (
                    "own_fixture_containers_remaining",
                    "own_fixture_volumes_remaining",
                    "own_fixture_image_tags_remaining",
                )
            ),
            "release_native_final_handoff_refused",
        )
        architectures[architecture] = saved
        for profile in ("aikey", "aiport"):
            for suffix in (".cdx.json", "-provenance.sigstore.json", "-sbom.sigstore.json"):
                name = profile + "-" + architecture + suffix
                raw = gate.read(folder / name, 32 * 1024 * 1024)
                if suffix == ".cdx.json":
                    require(
                        digest(raw) == saved["profiles"][profile]["sbom_sha256"],
                        "release_native_sbom_changed",
                    )
                (target / name).write_bytes(raw)
    package = Path(inputs) / "release-packages"
    names = {
        "local_aikey-" + plan["version"] + "-py3-none-any.whl",
        "local_aikey-" + plan["version"] + ".tar.gz",
        "package.cdx.json",
        "package-provenance.sigstore.json",
        "package-sbom.sigstore.json",
    }
    require({p.name for p in package.iterdir()} == names, "release_package_handoff_set_refused")
    for name in names:
        (target / name).write_bytes(gate.read(package / name, 64 * 1024 * 1024))
    tracked = set(gate.command(["git", "ls-files", "-z"], cwd=root).split("\0"))
    for name in ("THIRD_PARTY_NOTICES.txt", "CORRESPONDING_SOURCE.json"):
        if name in tracked:
            (target / name).write_bytes(gate.read(Path(root) / name, 16 * 1024 * 1024))
    # Validate actual package content BEFORE the first registry index write.
    publication.verify_packages(root, plan, target)
    package_bom(root, plan, target / "package.cdx.json")
    verify_staged(root, plan, target, architectures)
    intent = {
        "schema": "aikey-release-index-intent/1",
        **identity(plan),
        "references": {profile: tagged(plan, profile) for profile in ("aikey", "aiport")},
        "architectures": architectures,
    }
    emit(target.parent / "index-intent.json", intent)
    # Intent is produced separately before the workflow retains it. Index
    # creation is a separate CLI command, never coupled to this preparation.
    emit(
        target.parent / "candidate-inputs.json",
        {
            "schema": "aikey-release-candidate-inputs/1",
            **identity(plan),
            "architectures": architectures,
        },
    )
    return intent


def verify_staged(
    root, plan, target, architectures, *, run=gate.command, inspect=publication.docker_read
):
    """Validate signed upstream bytes before any dependent index write."""
    for name in (
        "local_aikey-" + plan["version"] + "-py3-none-any.whl",
        "local_aikey-" + plan["version"] + ".tar.gz",
    ):
        for sbom, bundle in (
            (False, "package-provenance.sigstore.json"),
            (True, "package-sbom.sigstore.json"),
        ):
            publication.verify_subject(
                root,
                plan,
                target,
                str(Path(target) / name),
                bundle,
                sbom=sbom,
                predicate_path=Path(target) / "package.cdx.json" if sbom else None,
                run=run,
            )
    for architecture in ("amd64", "arm64"):
        for profile in ("aikey", "aiport"):
            item = architectures[architecture]["profiles"][profile]
            require(
                item["reference"] == tagged(plan, profile, architecture)
                and isinstance(item["digest"], str)
                and re.fullmatch("sha256:[0-9a-f]{64}", item["digest"]),
                "release_staged_image_reference_refused",
            )
            subject = plan["images"][profile] + "@" + item["digest"]
            observed = inspect(subject, root)
            require(
                observed.get("schemaVersion") == 2
                and observed.get("config", {}).get("digest") == item["configuration_digest"],
                "release_staged_image_configuration_changed",
            )
            base = profile + "-" + architecture
            raw = gate.read(Path(target) / (base + ".cdx.json"), 16 * 1024 * 1024)
            bom = gate.load(raw)
            require(
                digest(raw) == item["sbom_sha256"]
                and bom.get("bomFormat") == "CycloneDX"
                and bom.get("metadata", {}).get("component", {}).get("version")
                == item["configuration_digest"].removeprefix("sha256:"),
                "release_staged_sbom_configuration_changed",
            )
            for sbom, bundle in (
                (False, base + "-provenance.sigstore.json"),
                (True, base + "-sbom.sigstore.json"),
            ):
                publication.verify_subject(
                    root,
                    plan,
                    target,
                    "oci://" + subject,
                    bundle,
                    sbom=sbom,
                    predicate_path=Path(target) / (base + ".cdx.json") if sbom else None,
                    run=run,
                )


def push_indexes(root, plan, destination, *, run=docker):
    target = Path(destination)
    value = gate.load(gate.read(target.parent / "candidate-inputs.json", 128 * 1024))
    require(
        value.get("schema") == "aikey-release-candidate-inputs/1"
        and all(value.get(key) == item for key, item in identity(plan).items()),
        "release_candidate_inputs_refused",
    )
    intent = gate.load(gate.read(target.parent / "index-intent.json", 128 * 1024))
    require(
        intent.get("schema") == "aikey-release-index-intent/1"
        and all(intent.get(key) == item for key, item in identity(plan).items())
        and intent.get("architectures") == value.get("architectures")
        and intent.get("references")
        == {profile: tagged(plan, profile) for profile in ("aikey", "aiport")},
        "release_index_intent_changed",
    )
    require(not (target.parent / "indexes.json").exists(), "release_index_attempt_already_recorded")
    publication.verify_packages(root, plan, target)
    package_bom(root, plan, target / "package.cdx.json")
    verify_staged(root, plan, target, value["architectures"])
    emit(
        target.parent / "index-started.json",
        {"schema": "aikey-release-index-started/1", **identity(plan)},
    )
    images = {}
    for profile in ("aikey", "aiport"):
        members = {}
        for architecture in ("amd64", "arm64"):
            item = value["architectures"][architecture]["profiles"][profile]
            require(
                item["reference"] == tagged(plan, profile, architecture)
                and isinstance(item["digest"], str)
                and re.fullmatch("sha256:[0-9a-f]{64}", item["digest"]),
                "release_staged_image_reference_refused",
            )
            observed = publication.docker_read(plan["images"][profile] + "@" + item["digest"], root)
            require(
                observed.get("config", {}).get("digest") == item["configuration_digest"],
                "release_staged_image_configuration_changed",
            )
            members[architecture] = item
        acknowledged = True
        try:
            run(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "create",
                    "--tag",
                    tagged(plan, profile),
                    *(
                        plan["images"][profile] + "@" + members[arch]["digest"]
                        for arch in ("amd64", "arm64")
                    ),
                ],
                root,
                mutate=True,
                timeout=120,
            )
        except publication.PublicationError:
            acknowledged = False
        index_digest, observed = observed_tag(root, tagged(plan, profile), run=run)
        descriptors = observed.get("manifests", [])
        require(
            observed.get("schemaVersion") == 2
            and isinstance(descriptors, list)
            and len(descriptors) == 2
            and {
                row.get("platform", {}).get("architecture"): row.get("digest")
                for row in descriptors
            }
            == {arch: item["digest"] for arch, item in members.items()}
            and all(row.get("platform", {}).get("os") == "linux" for row in descriptors),
            "release_pushed_index_unverified",
        )
        images[profile] = {
            "digest": index_digest,
            "write_acknowledged": acknowledged,
            "architectures": members,
        }
    emit(
        target.parent / "indexes.json",
        {"schema": "aikey-release-indexes/1", **identity(plan), "images": images},
    )
    return images


def finalize(root, plan, destination):
    target = Path(destination)
    saved = gate.load(gate.read(target.parent / "indexes.json", 128 * 1024))
    require(
        saved.get("schema") == "aikey-release-indexes/1"
        and all(saved.get(key) == item for key, item in identity(plan).items()),
        "release_indexes_handoff_refused",
    )
    images = {}
    for profile, image in saved["images"].items():
        members = {}
        for arch, item in image["architectures"].items():
            base = profile + "-" + arch
            members[arch] = {
                "digest": item["digest"],
                "configuration_digest": item["configuration_digest"],
                "sbom": base + ".cdx.json",
                "provenance": base + "-provenance.sigstore.json",
                "sbom_attestation": base + "-sbom.sigstore.json",
            }
        images[profile] = {
            "digest": image["digest"],
            "architectures": members,
            "provenance": profile + "-index-provenance.sigstore.json",
        }
    require(
        publication.allowed_assets(plan) - {"SHA256SUMS"}
        <= {p.name for p in target.iterdir()}
        <= (publication.allowed_assets(plan) - {"SHA256SUMS"})
        | {"THIRD_PARTY_NOTICES.txt", "CORRESPONDING_SOURCE.json"},
        "release_candidate_file_set_refused",
    )
    files = {
        p.name: {"bytes": len(raw), "sha256": digest(raw)}
        for p in target.iterdir()
        if (raw := gate.read(p, 64 * 1024 * 1024))
    }
    sums = "".join(
        item["sha256"] + "  " + name + "\n" for name, item in sorted(files.items())
    ).encode()
    fd = os.open(target / "SHA256SUMS", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(sums)
    files["SHA256SUMS"] = {"bytes": len(sums), "sha256": digest(sums)}
    emit(
        target / "release.json",
        {
            "schema": "aikey-release-assets/1",
            **{
                key: plan[key]
                for key in (
                    "source_revision",
                    "source_snapshot_sha256",
                    "tag",
                    "version",
                    "workflow_run_id",
                )
            },
            "images": images,
            "files": files,
        },
    )


def package_bom(root, plan, path):
    """Check the maintained CycloneDX producer against the runtime lock."""
    value = gate.load(gate.read(path, 16 * 1024 * 1024))
    require(
        value.get("bomFormat") == "CycloneDX" and isinstance(value.get("components"), list),
        "release_package_sbom_refused",
    )
    rows = value["components"] + [value.get("metadata", {}).get("component", {})]
    versions = {}
    for row in rows:
        if (
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and isinstance(row.get("version"), str)
        ):
            versions.setdefault(inventory.normalized(row["name"]), set()).add(row["version"])
    expected = {"local-aikey": plan["version"]}
    for line in gate.read(Path(root) / "requirements-runtime.lock").decode().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.+-]+)==([A-Za-z0-9.+!-]+)", line)
        require(match is not None, "release_runtime_lock_refused")
        expected[inventory.normalized(match[1])] = match[2]
    require(
        all(versions.get(name) == {version} for name, version in expected.items()),
        "release_package_sbom_runtime_mismatch",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "image-intent",
            "push-images",
            "stage-assets",
            "indexes",
            "push-indexes",
            "finalize",
            "check",
        ),
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--results", type=Path)
    parser.add_argument("--qualified", type=Path)
    parser.add_argument("--intent", type=Path)
    parser.add_argument("--staged", type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        require(args.release_id > 0, "release_reservation_required")
        plan = dict(publication.fresh(root, args.tag), reservation_id=args.release_id)
        publication.original(root, plan)
        if args.command == "image-intent":
            image_intent(
                root,
                plan,
                args.results,
                args.architecture,
                args.destination,
                qualified=args.qualified,
            )
        elif args.command == "push-images":
            value = push_images(
                root, plan, args.intent, args.architecture, args.results, args.destination
            )
            output(
                {profile + "_name": plan["images"][profile] for profile in ("aikey", "aiport")}
                | {
                    profile + "_digest": item["digest"]
                    for profile, item in value["profiles"].items()
                }
            )
        elif args.command == "stage-assets":
            stage_assets(root, plan, args.results, args.staged, args.architecture, args.destination)
        elif args.command == "indexes":
            indexes(root, plan, args.inputs, args.destination)
        elif args.command == "push-indexes":
            images = push_indexes(root, plan, args.destination)
            output(
                {profile + "_name": plan["images"][profile] for profile in ("aikey", "aiport")}
                | {profile + "_digest": item["digest"] for profile, item in images.items()}
            )
        elif args.command == "check":
            manifest = publication.checked_assets(args.destination, plan)
            publication.verify_signatures(root, plan, args.destination, manifest)
            publication.verify_packages(root, plan, args.destination)
            package_bom(root, plan, args.destination / "package.cdx.json")
        else:
            finalize(root, plan, args.destination)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        print(
            json.dumps(
                {
                    "error": "release_candidate_unavailable",
                    "automatic_retry": False,
                    "publication_outcome": "not_asserted",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

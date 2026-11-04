"""Isolated producer/consumer handoffs. Docker and Sigstore are explicit doubles."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from test_release_assets import assets, fake_registry, real_packages  # noqa: F401
from test_release_prepare import tagged_source  # noqa: F401
from test_release_publication import plan

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location(
    "release_candidate_under_test", TOOLS / "release_candidate.py"
)
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)
sys.path.pop(0)


@pytest.fixture
def native_attempt(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "src/aikey").mkdir(parents=True)
    (root / "src/aikey/__init__.py").write_text('"""FICTIONAL source."""\n')
    (root / "pyproject.toml").write_text('[project]\nversion="0.1.0rc1"\n')
    (root / "requirements-runtime.lock").write_text("fictional-runtime==1.0\n")
    (root / "requirements-build.lock").write_text("setuptools==84.0.0\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    packet = dict(plan(), reservation_id=99)
    results = tmp_path / "results"
    results.mkdir()
    qualified = tmp_path / "qualified"
    qualified.mkdir()
    qualified_profiles = {}
    arch = "amd64"
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    monkeypatch.setenv("INSTALL_NONCE", "123-1-amd64")
    inspected = {}
    for profile in ("aikey", "aiport"):
        image_id = "sha256:" + candidate.digest(profile.encode())
        labels = {
            "org.opencontainers.image.revision": packet["source_revision"],
            "org.opencontainers.image.source": "https://github.com/" + candidate.gate.REPOSITORY,
            "org.local-aikey.install-run": "123-1-amd64",
            "org.local-aikey.install-profile": profile,
        }
        inspected[profile] = {
            "Id": image_id,
            "Os": "linux",
            "Architecture": arch,
            "Config": {"User": "10001:10001", "Labels": labels},
        }
        bom = {
            "bomFormat": "CycloneDX",
            "components": [
                {"type": "library", "name": "FICTIONAL-runtime", "version": "1.0", "licenses": []}
            ],
            "metadata": {"component": {"name": "sha256", "version": image_id[7:]}},
        }
        raw = json.dumps(bom).encode()
        (results / (profile + "-sbom.cdx.json")).write_bytes(raw)
        receipt = {
            "schema": candidate.inventory.RECEIPT,
            "revision": packet["source_revision"],
            "architecture": arch,
            "profile": profile,
            "nonce": "123-1-amd64",
            "scanner_version": candidate.inventory.VERSION,
            "conversion_verified": True,
            "runtime_lock_packages_verified": 3,
            "sbom_sha256": candidate.digest(raw),
            "image_id": image_id,
        }
        (results / (profile + "-inventory.json")).write_text(json.dumps(receipt))
        qualified_profiles[profile] = receipt
        (qualified / (profile + "-sbom.cdx.json")).write_bytes(raw)
        for case in candidate.installation.CASES:
            positive = case in ("create", "reopen")
            if positive:
                response = {
                    "schema": candidate.installation.PHASE,
                    "phase_passed": True,
                    "profile": profile,
                    "phase": case,
                    "architecture": arch,
                    "revision": packet["source_revision"],
                    "version": packet["version"],
                    "module_count": 1,
                    "saved_private_files": 8,
                    "loopback_lab_checks": 20 if case == "create" else 0,
                    "real_controller_contacted": False,
                    "real_inference_performed": False,
                    "native_verification": "needs_evidence",
                }
            else:
                response = {
                    "schema": candidate.installation.PHASE,
                    "phase_passed": False,
                    "error": {
                        "wrong-source": "container_installed_source_mismatch",
                        "wrong-arch": "container_architecture_mismatch",
                    }[case],
                    "native_verification": "needs_evidence",
                }
            record = candidate.installation.record(
                response, 0 if positive else 2, profile, case, arch, packet["source_revision"]
            )
            candidate.emit(results / (profile + "-" + case + ".json"), record)
    (qualified / "image-inventory.json").write_text(
        json.dumps(
            {
                "schema": "aikey-image-inventory-summary/1",
                "revision": packet["source_revision"],
                "architecture": arch,
                "cleanup_verified": True,
                "profiles": qualified_profiles,
            }
        )
    )
    (qualified / "summary.json").write_text(
        json.dumps(
            {
                "revision": packet["source_revision"],
                "architecture": arch,
                "passed": True,
                "cases_finished": 8,
            }
        )
    )
    calls = []

    def docker(argv, root, **kwargs):
        calls.append(argv)
        assert argv[:3] == ["docker", "image", "inspect"]
        image = next(
            value
            for profile, value in inspected.items()
            if argv[3] in (value["Id"], "local-aikey-install:123-1-amd64-" + profile)
        )
        return json.dumps([image]).encode()

    return root, packet, results, inspected, calls, docker


def test_pre_push_intent_is_private_create_only_and_not_a_cleanup_claim(native_attempt):
    root, packet, results, _, calls, docker = native_attempt
    destination = root.parent / "intent.json"
    value = candidate.image_intent(
        root,
        packet,
        results,
        "amd64",
        destination,
        qualified=results.parent / "qualified",
        run=docker,
    )
    assert value["schema"] == "aikey-release-image-intent/1"
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert "cleanup_verified" not in value
    assert not (results / "cleanup.json").exists()
    assert all(argv[:3] == ["docker", "image", "inspect"] for argv in calls)
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        candidate.image_intent(
            root,
            packet,
            results,
            "amd64",
            destination,
            qualified=results.parent / "qualified",
            run=docker,
        )
    assert before == destination.read_bytes()


@pytest.mark.parametrize(
    "fault",
    [
        "incomplete_case",
        "wrong_runner",
        "wrong_nonce",
        "wrong_version",
        "missing_module",
        "changed_image",
        "changed_bom",
    ],
)
def test_native_handoff_rejects_mismatch_before_any_image_write(native_attempt, monkeypatch, fault):
    root, packet, results, inspected, calls, docker = native_attempt
    if fault == "incomplete_case":
        (results / "aikey-reopen.json").unlink()
    elif fault == "wrong_runner":
        monkeypatch.setenv("RUNNER_ARCH", "ARM64")
    elif fault == "wrong_nonce":
        monkeypatch.setenv("INSTALL_NONCE", "foreign-1-amd64")
    elif fault == "wrong_version":
        packet["version"] = "0.1.1"
    elif fault == "missing_module":
        (root / "src/aikey/extra.py").write_text("FICTIONAL=1\n")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
    elif fault == "changed_image":
        inspected["aikey"]["Id"] = "sha256:" + "f" * 64
    else:
        (results / "aikey-sbom.cdx.json").write_text("{}")
    destination = root.parent / "intent.json"
    with pytest.raises((ValueError, OSError)):
        candidate.image_intent(
            root,
            packet,
            results,
            "amd64",
            destination,
            qualified=results.parent / "qualified",
            run=docker,
        )
    assert not destination.exists()
    assert all(argv[:3] == ["docker", "image", "inspect"] for argv in calls)


@pytest.mark.parametrize(
    "fault", ["none", "lost_ack", "unobserved", "wrong_config", "stale_intent"]
)
def test_registry_push_ack_is_not_authority_and_is_never_retried(native_attempt, fault):
    root, packet, results, inspected, _, inspect = native_attempt
    intent_path = root.parent / "intent.json"
    candidate.image_intent(
        root,
        packet,
        results,
        "amd64",
        intent_path,
        qualified=results.parent / "qualified",
        run=inspect,
    )
    calls = []

    def docker(argv, root, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return inspect(argv, root)
        if argv[:3] == ["docker", "image", "push"]:
            if fault in ("lost_ack", "unobserved"):
                raise candidate.publication.PublicationError("release_registry_write_uncertain")
        elif argv[:3] == ["docker", "buildx", "imagetools"]:
            if fault == "unobserved":
                raise candidate.publication.PublicationError(
                    "release_registry_observation_unavailable"
                )
            profile = next(
                profile
                for profile in ("aikey", "aiport")
                if argv[-1] == candidate.tagged(packet, profile, "amd64")
            )
            return json.dumps(
                {
                    "schemaVersion": 2,
                    "config": {
                        "digest": "sha256:" + "0" * 64
                        if fault == "wrong_config"
                        else inspected[profile]["Id"]
                    },
                }
            ).encode()
        else:
            assert argv[:3] in (["docker", "image", "tag"], ["docker", "image", "rm"])
        return b""

    destination = root.parent / "staged.json"
    if fault == "stale_intent":
        packet["workflow_run_id"] = "124"
    if fault in ("none", "lost_ack"):
        result = candidate.push_images(
            root, packet, intent_path, "amd64", results, destination, run=docker
        )
        assert all(
            item["write_acknowledged"] is (fault == "none") for item in result["profiles"].values()
        )
        assert len([argv for argv in calls if argv[:3] == ["docker", "image", "push"]]) == 2
        assert len([argv for argv in calls if argv[:3] == ["docker", "image", "rm"]]) == 2
        with pytest.raises(ValueError, match="already_recorded"):
            candidate.push_images(
                root, packet, intent_path, "amd64", results, destination, run=docker
            )
    else:
        with pytest.raises(ValueError):
            candidate.push_images(
                root, packet, intent_path, "amd64", results, destination, run=docker
            )
        assert not destination.exists()
        assert len([argv for argv in calls if argv[:3] == ["docker", "image", "push"]]) == (
            0 if fault == "stale_intent" else 1
        )


def test_final_manifest_producer_and_actual_file_consumer_agree(assets):  # noqa: F811
    root, packet, directory, manifest = assets
    saved = {"schema": "aikey-release-indexes/1", **candidate.identity(packet), "images": {}}
    for profile, image in manifest["images"].items():
        saved["images"][profile] = {
            "digest": image["digest"],
            "architectures": image["architectures"],
        }
    candidate.emit(directory.parent / "indexes.json", saved)
    (directory / "release.json").unlink()
    (directory / "release-provenance.sigstore.json").unlink()
    (directory / "SHA256SUMS").unlink()
    candidate.finalize(root, packet, directory)
    (directory / "release-provenance.sigstore.json").write_text("FICTIONAL not signed")
    assert candidate.publication.checked_assets(directory, packet)["images"] == manifest["images"]
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(ValueError):
        candidate.finalize(root, packet, directory)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_finalization_cannot_absorb_unknown_files_into_a_signed_release(assets):  # noqa: F811
    root, packet, directory, manifest = assets
    saved = {
        "schema": "aikey-release-indexes/1",
        **candidate.identity(packet),
        "images": manifest["images"],
    }
    candidate.emit(directory.parent / "indexes.json", saved)
    for name in ("release.json", "SHA256SUMS", "release-provenance.sigstore.json"):
        (directory / name).unlink()
    (directory / "unreviewed.txt").write_text("FICTIONAL unexpected payload")
    with pytest.raises(ValueError, match="file_set"):
        candidate.finalize(root, packet, directory)
    assert not (directory / "release.json").exists()


def test_package_sbom_requires_all_pinned_runtime_versions(tmp_path):
    (tmp_path / "requirements-runtime.lock").write_text("typing_extensions==4.16.0\n")
    packet = plan()
    path = tmp_path / "package.cdx.json"
    value = {
        "bomFormat": "CycloneDX",
        "components": [{"name": "typing_extensions", "version": "4.16.0"}],
        "metadata": {"component": {"name": "local-aikey", "version": packet["version"]}},
    }
    path.write_text(json.dumps(value))
    candidate.package_bom(tmp_path, packet, path)
    value["components"][0]["version"] = "4.15.0"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="runtime_mismatch"):
        candidate.package_bom(tmp_path, packet, path)


def test_actual_workflow_all_shells_parse_and_writers_follow_reviewed_stages(tmp_path):
    workflow = yaml.safe_load((TOOLS.parent / ".github/workflows/publish-release.yml").read_text())
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] is False
    jobs = workflow["jobs"]
    assert (
        jobs["reserve"]["environment"] == "release-stage" and jobs["reserve"]["needs"] == "qualify"
    )
    assert jobs["packages"]["needs"] == jobs["images"]["needs"] == "reserve"
    assert jobs["publish"]["environment"] == "release"
    assert jobs["publish"]["needs"] == ["reserve", "assemble"]
    assert jobs["qualify"]["permissions"] == {"contents": "read", "actions": "read"}
    assert jobs["publish"]["permissions"].get("id-token") is None
    for name, job in jobs.items():
        for index, step in enumerate(job["steps"]):
            if "uses" in step:
                ref = step["uses"].rsplit("@", 1)[1]
                assert len(ref) == 40 and all(ch in "0123456789abcdef" for ch in ref)
                if step["uses"].startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
                if step["uses"].startswith("actions/attest@"):
                    assert step["with"]["create-storage-record"] is False
                    assert step["with"]["push-to-registry"] is False
            if "run" in step:
                script = tmp_path / (name + "-" + str(index) + ".sh")
                script.write_text(step["run"])
                subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)


@pytest.fixture
def assembly(assets, real_packages, monkeypatch):  # noqa: F811
    _, _, asset_folder, manifest = assets
    root, packet, packages = real_packages
    packet.update(reservation_id=99)
    inputs = root.parent / "inputs"
    inputs.mkdir()
    package_folder = inputs / "release-packages"
    package_folder.mkdir()
    for path in packages.iterdir():
        (package_folder / path.name).write_bytes(path.read_bytes())
    for name in (
        "package.cdx.json",
        "package-provenance.sigstore.json",
        "package-sbom.sigstore.json",
    ):
        (package_folder / name).write_bytes((asset_folder / name).read_bytes())
    for arch in ("amd64", "arm64"):
        folder = inputs / ("release-images-" + arch)
        folder.mkdir()
        profiles = {}
        for profile, image in manifest["images"].items():
            descriptor = image["architectures"][arch]
            raw = (asset_folder / descriptor["sbom"]).read_bytes()
            for key in ("sbom", "provenance", "sbom_attestation"):
                (folder / descriptor[key]).write_bytes(
                    (asset_folder / descriptor[key]).read_bytes()
                )
            profiles[profile] = {
                "digest": descriptor["digest"],
                "configuration_digest": descriptor["configuration_digest"],
                "reference": candidate.tagged(packet, profile, arch),
                "sbom_sha256": candidate.digest(raw),
                "write_acknowledged": True,
            }
        candidate.emit(
            folder / ("staged-" + arch + ".json"),
            {
                "schema": "aikey-release-staged-images/1",
                **candidate.identity(packet, architecture=arch),
                "profiles": profiles,
            },
        )
        candidate.emit(
            folder / ("install-" + arch + ".json"),
            {
                "schema": candidate.installation.SUMMARY,
                "revision": packet["source_revision"],
                "architecture": arch,
                "passed": True,
                "cases_finished": 8,
                "own_fixture_containers_remaining": 0,
                "own_fixture_volumes_remaining": 0,
                "own_fixture_image_tags_remaining": 0,
            },
        )
    actual = candidate.verify_staged
    inspector = fake_registry(manifest)

    def verify(root, packet, target, architectures):
        return actual(
            root,
            packet,
            target,
            architectures,
            inspect=inspector,
            run=lambda *a, **kw: "FICTIONAL verified transport",
        )

    monkeypatch.setattr(candidate, "verify_staged", verify)
    monkeypatch.setattr(candidate.publication, "docker_read", inspector)
    destination = root.parent / "release-candidate"
    return root, packet, inputs, destination, manifest


@pytest.mark.parametrize("fault", ["none", "lost_ack", "unobserved"])
def test_complete_index_handoff_uses_real_archives_and_standard_verifier_contract(assembly, fault):
    root, packet, inputs, destination, manifest = assembly
    intent = candidate.indexes(root, packet, inputs, destination)
    assert intent["schema"] == "aikey-release-index-intent/1"
    writes = []

    def docker(argv, root, **kwargs):
        profile = next(p for p in ("aikey", "aiport") if candidate.tagged(packet, p) in argv)
        if argv[3] == "create":
            writes.append(argv)
            if fault in ("lost_ack", "unobserved"):
                raise candidate.publication.PublicationError("release_registry_write_uncertain")
            return b""
        assert argv[3] == "inspect"
        if fault == "unobserved":
            raise candidate.publication.PublicationError("release_registry_observation_unavailable")
        return json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {"digest": item["digest"], "platform": {"os": "linux", "architecture": arch}}
                    for arch, item in manifest["images"][profile]["architectures"].items()
                ],
            }
        ).encode()

    if fault == "unobserved":
        with pytest.raises(ValueError):
            candidate.push_indexes(root, packet, destination, run=docker)
        assert len(writes) == 1 and not (destination.parent / "indexes.json").exists()
        with pytest.raises(FileExistsError):
            candidate.push_indexes(root, packet, destination, run=docker)
        assert len(writes) == 1
        return
    result = candidate.push_indexes(root, packet, destination, run=docker)
    assert len(writes) == 2 and all(
        item["write_acknowledged"] is (fault == "none") for item in result.values()
    )
    for profile in ("aikey", "aiport"):
        (destination / (profile + "-index-provenance.sigstore.json")).write_text(
            "FICTIONAL not signed"
        )
    candidate.finalize(root, packet, destination)
    (destination / "release-provenance.sigstore.json").write_text("FICTIONAL not signed")
    observed = candidate.publication.checked_assets(destination, packet)
    assert observed["source_revision"] == packet["source_revision"]
    assert set(observed["images"]) == {"aikey", "aiport"}
    assert len(observed["files"]) == 20


def test_index_creation_cannot_start_when_upstream_signature_check_fails(assembly, monkeypatch):
    root, packet, inputs, destination, _ = assembly
    candidate.indexes(root, packet, inputs, destination)

    def refused(*a, **kw):
        raise candidate.publication.PublicationError("FICTIONAL verifier refused")

    monkeypatch.setattr(candidate, "verify_staged", refused)
    writes = []
    with pytest.raises(ValueError):
        candidate.push_indexes(root, packet, destination, run=lambda *a, **kw: writes.append(a))
    assert writes == [] and not (destination.parent / "index-started.json").exists()


def test_qualified_catalog_drift_refuses_before_intent_or_registry_mutation(native_attempt):
    root, packet, results, _, calls, docker = native_attempt
    path = results / "aikey-sbom.cdx.json"
    value = json.loads(path.read_text())
    value["components"][0]["version"] = "2.0"
    path.write_text(json.dumps(value))
    receipt_path = results / "aikey-inventory.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["sbom_sha256"] = candidate.digest(path.read_bytes())
    receipt_path.write_text(json.dumps(receipt))
    destination = root.parent / "intent.json"
    with pytest.raises(ValueError, match="catalog_changed"):
        candidate.image_intent(
            root,
            packet,
            results,
            "amd64",
            destination,
            qualified=results.parent / "qualified",
            run=docker,
        )
    assert not destination.exists() and calls == []

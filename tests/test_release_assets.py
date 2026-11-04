"""Actual file/archive boundaries with FICTIONAL signatures and provider doubles.

No test authorizes a real release or claims to exercise Sigstore cryptography.
"""

import copy
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

from test_release_prepare import tagged_source  # noqa: F401 -- reusable real Git fixture
from test_release_publication import publication, plan


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def assets(tmp_path):
    packet = dict(plan(), reservation_id=99, prerelease=True, source_date_epoch=1700000000)
    directory = tmp_path / "assets"
    directory.mkdir()
    for name in publication.allowed_assets(packet):
        (directory / name).write_bytes(b"FICTIONAL test-only signature/package bytes\n")
    images = {}
    for profile in ("aikey", "aiport"):
        image = {
            "digest": "sha256:" + sha(profile.encode()),
            "provenance": profile + "-index-provenance.sigstore.json",
            "architectures": {},
        }
        for arch in ("amd64", "arm64"):
            base = profile + "-" + arch
            config = "sha256:" + sha((base + "-config").encode())
            image["architectures"][arch] = {
                "digest": "sha256:" + sha(base.encode()),
                "configuration_digest": config,
                "sbom": base + ".cdx.json",
                "provenance": base + "-provenance.sigstore.json",
                "sbom_attestation": base + "-sbom.sigstore.json",
            }
            (directory / (base + ".cdx.json")).write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "metadata": {
                            "component": {
                                "name": "sha256",
                                "version": config.removeprefix("sha256:"),
                            }
                        },
                    }
                )
            )
        images[profile] = image
    manifest = {
        key: packet[key]
        for key in (
            "source_revision",
            "source_snapshot_sha256",
            "tag",
            "version",
            "workflow_run_id",
        )
    }
    manifest.update(schema="aikey-release-assets/1", images=images)
    (directory / "package.cdx.json").write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "components": [],
                "metadata": {"component": {"name": "local-aikey", "version": packet["version"]}},
            }
        )
    )
    for name in (
        "package",
        *(profile + "-" + arch for profile in ("aikey", "aiport") for arch in ("amd64", "arm64")),
    ):
        predicate = json.loads((directory / (name + ".cdx.json")).read_text())
        payload = base64.b64encode(
            json.dumps(
                {"predicateType": "https://cyclonedx.org/bom", "predicate": predicate}
            ).encode()
        ).decode()
        (directory / (name + "-sbom.sigstore.json")).write_text(
            json.dumps({"FICTIONAL": "NOT SIGNED", "dsseEnvelope": {"payload": payload}})
        )
    (directory / "release-provenance.sigstore.json").write_text("FICTIONAL bundle, not a signature")
    seal(directory, manifest)
    return tmp_path, packet, directory, manifest


def seal(directory, manifest):
    files = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha(path.read_bytes())}
        for path in directory.iterdir()
        if path.name not in ("release.json", "release-provenance.sigstore.json", "SHA256SUMS")
    }
    (directory / "SHA256SUMS").write_text(
        "".join(info["sha256"] + "  " + name + "\n" for name, info in sorted(files.items()))
    )
    raw = (directory / "SHA256SUMS").read_bytes()
    files["SHA256SUMS"] = {"bytes": len(raw), "sha256": sha(raw)}
    manifest["files"] = files
    (directory / "release.json").write_text(json.dumps(manifest, sort_keys=True))


def test_actual_captured_bytes_are_immutable_and_private_stage_is_removed(assets):
    root, packet, directory, expected = assets
    with publication.snapshot_assets(directory, root, packet) as captured:
        assert captured != directory and captured.is_dir()
        assert publication.checked_assets(captured, packet) == expected
        original = (captured / "package.cdx.json").read_bytes()
        (directory / "package.cdx.json").write_text("changed producer after capture")
        assert (captured / "package.cdx.json").read_bytes() == original
        assert publication.checked_assets(captured, packet) == expected
    assert not captured.exists()


@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "missing",
        "symlink",
        "hardlink",
        "directory",
        "changed_bytes",
        "wrong_source",
        "wrong_run",
        "duplicate_json",
        "wrong_config",
        "wrong_checksums",
    ],
)
def test_actual_file_set_refuses_foreign_or_changed_inputs_before_upload(assets, fault):
    root, packet, directory, manifest = assets
    if fault == "extra":
        (directory / ".env").write_text("FICTIONAL must not be copied")
    elif fault == "missing":
        (directory / "package.cdx.json").unlink()
    elif fault in ("symlink", "hardlink", "directory"):
        path = directory / "package.cdx.json"
        path.unlink()
        if fault == "symlink":
            path.symlink_to(root / "unread-secret")
        elif fault == "hardlink":
            import os

            other = root / "linked"
            other.write_text("FICTIONAL shared file")
            os.link(other, path)
        else:
            path.mkdir()
    elif fault == "changed_bytes":
        (directory / "package.cdx.json").write_text("changed without new manifest")
    elif fault in ("wrong_source", "wrong_run"):
        manifest["source_revision" if fault == "wrong_source" else "workflow_run_id"] = "999"
        seal(directory, manifest)
    elif fault == "duplicate_json":
        raw = (directory / "release.json").read_text()
        (directory / "release.json").write_text('{"schema":"other",' + raw[1:])
    elif fault == "wrong_config":
        (directory / "aikey-arm64.cdx.json").write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"component": {"name": "sha256", "version": "f" * 64}},
                }
            )
        )
        seal(directory, manifest)
    else:
        (directory / "SHA256SUMS").write_text("wrong but internally hashed checksums\n")
        raw = (directory / "SHA256SUMS").read_bytes()
        manifest["files"]["SHA256SUMS"] = {"bytes": len(raw), "sha256": sha(raw)}
        (directory / "release.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        with publication.snapshot_assets(directory, root, packet) as captured:
            publication.checked_assets(captured, packet)
    assert not list(root.parent.glob("release-assets-*"))


def fake_registry(manifest):
    def inspect(reference, root):
        digest = reference.rsplit("@", 1)[1]
        for image in manifest["images"].values():
            if digest == image["digest"]:
                return {
                    "schemaVersion": 2,
                    "manifests": [
                        {
                            "digest": descriptor["digest"],
                            "platform": {"os": "linux", "architecture": arch},
                        }
                        for arch, descriptor in image["architectures"].items()
                    ],
                }
            for descriptor in image["architectures"].values():
                if digest == descriptor["digest"]:
                    return {
                        "schemaVersion": 2,
                        "config": {"digest": descriptor["configuration_digest"]},
                    }
        raise AssertionError("Unexpected image")

    return inspect


def test_standard_verifier_receives_exact_source_identity_and_every_subject(assets):
    root, packet, directory, manifest = assets
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ["gh", "attestation", "verify"]
        for option, expected in (
            ("--repo", publication.gate.REPOSITORY),
            ("--source-digest", packet["source_revision"]),
            ("--signer-digest", packet["source_revision"]),
            ("--source-ref", "refs/heads/main"),
            (
                "--signer-workflow",
                publication.gate.REPOSITORY + "/.github/workflows/publish-release.yml",
            ),
            ("--cert-oidc-issuer", "https://token.actions.githubusercontent.com"),
        ):
            assert argv[argv.index(option) + 1] == expected
        assert "--deny-self-hosted-runners" in argv
        assert Path(argv[argv.index("--bundle") + 1]).is_file()
        return "FICTIONAL transport acknowledgement, not signature proof"

    publication.verify_signatures(
        root, packet, directory, manifest, run=run, inspect=fake_registry(manifest)
    )
    assert len(calls) == 16
    assert sum("--predicate-type" in argv for argv in calls) == 6


@pytest.mark.parametrize(
    "fault", ["signature", "architecture", "extra_platform", "index_digest", "config"]
)
def test_failed_signature_or_registry_binding_cannot_be_ignored(assets, fault):
    root, packet, directory, manifest = assets
    seen = []
    actual = fake_registry(manifest)

    def inspect(reference, root):
        value = copy.deepcopy(actual(reference, root))
        if "manifests" in value:
            if fault == "architecture":
                value["manifests"][0]["platform"]["os"] = "windows"
            elif fault == "extra_platform":
                value["manifests"].append(copy.deepcopy(value["manifests"][0]))
            elif fault == "index_digest":
                value["manifests"][0]["digest"] = "sha256:" + "f" * 64
        elif fault == "config":
            value["config"]["digest"] = "sha256:" + "f" * 64
        return value

    def run(argv, **kwargs):
        seen.append(argv)
        if fault == "signature":
            raise publication.gate.PreparationError("release_metadata_unavailable")
        return ""

    with pytest.raises(ValueError):
        publication.verify_signatures(root, packet, directory, manifest, run=run, inspect=inspect)
    assert seen


@pytest.fixture
def real_packages(tagged_source, tmp_path):  # noqa: F811 -- imported pytest fixture
    root, _, _, _, _ = tagged_source
    module = root / "src/aikey/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text('"""FICTIONAL package source fixture."""\nVALUE = 42\n')
    (root / "requirements-runtime.lock").write_text(
        "# FICTIONAL fixture: no runtime dependencies\n"
    )

    def git(*args):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "user.name=Fictional",
                    "-c",
                    "user.email=fixture@example.invalid",
                    *args,
                ],
                cwd=root,
            )
            .decode()
            .strip()
        )

    git("add", ".")
    spec = importlib.util.spec_from_file_location(
        "fictional_audit", root / "tools/community_release_check.py"
    )
    community = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(community)
    path = root / "docs/legal/release-evidence.json"
    record = json.loads(path.read_text())
    fingerprint = community.source_snapshot(root, community.tracked_paths(root))
    record["reviewed_snapshot_sha256"] = fingerprint
    path.write_text(json.dumps(record))
    git("add", ".")
    git("commit", "-qm", "Fictional package source, not an approval")
    packet = dict(
        plan(),
        source_revision=git("rev-parse", "HEAD"),
        source_snapshot_sha256=fingerprint,
        source_date_epoch=int(git("show", "-s", "--format=%ct", "HEAD")),
    )
    directory = tmp_path / "packages"
    directory.mkdir()
    make_packages(root, packet, directory)
    return root, packet, directory


def make_packages(root, packet, directory, *, changed_module=False, extra=None, owner=0):
    version = packet["version"]
    with zipfile.ZipFile(
        directory / ("local_aikey-" + version + "-py3-none-any.whl"), "w"
    ) as wheel:
        wheel.writestr(
            "aikey/__init__.py",
            b"changed" if changed_module else (root / "src/aikey/__init__.py").read_bytes(),
        )
        wheel.writestr(
            "local_aikey-" + version + ".dist-info/licenses/LICENSE",
            (root / "LICENSE").read_bytes(),
        )
        wheel.writestr(
            "local_aikey-" + version + ".dist-info/METADATA",
            "Metadata-Version: 2.4\nName: local-aikey\nVersion: " + version + "\n",
        )
        prefix = "local_aikey-" + version + ".dist-info/"
        wheel.writestr(
            prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        records = io.StringIO()
        writer = csv.writer(records, lineterminator="\n")
        for name in wheel.namelist():
            raw = wheel.read(name)
            writer.writerow(
                (
                    name,
                    "sha256="
                    + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="),
                    str(len(raw)),
                )
            )
        writer.writerow((prefix + "RECORD", "", ""))
        wheel.writestr(prefix + "RECORD", records.getvalue())
    originals = ["LICENSE", "pyproject.toml", "src/aikey/__init__.py"]
    entries = {name: (root / name).read_bytes() for name in originals}
    if extra:
        entries[extra] = b"FICTIONAL unexpected payload"
    with tarfile.open(directory / ("local_aikey-" + version + ".tar.gz"), "w:gz") as sdist:
        for name, raw in entries.items():
            item = tarfile.TarInfo("local_aikey-" + version + "/" + name)
            item.size, item.mtime, item.uid = len(raw), packet["source_date_epoch"], owner
            sdist.addfile(item, io.BytesIO(raw))


def test_real_archive_consumer_uses_non_audit_legal_gate_and_exact_source(real_packages):
    root, packet, directory = real_packages
    before = {path.name: sha(path.read_bytes()) for path in directory.iterdir()}
    publication.verify_packages(root, packet, directory)
    assert before == {path.name: sha(path.read_bytes()) for path in directory.iterdir()}


@pytest.mark.parametrize(
    "fault", ["changed_module", "extra_payload", "builder_uid", "truncated", "stale_review"]
)
def test_real_archive_refusals_are_not_normalized_into_success(real_packages, fault):
    root, packet, directory = real_packages
    if fault == "changed_module":
        make_packages(root, packet, directory, changed_module=True)
    elif fault == "extra_payload":
        make_packages(root, packet, directory, extra="unexpected.py")
    elif fault == "builder_uid":
        make_packages(root, packet, directory, owner=1234)
    elif fault == "truncated":
        path = next(directory.glob("*.tar.gz"))
        path.write_bytes(path.read_bytes()[:10])
    else:
        path = root / "docs/legal/release-evidence.json"
        value = json.loads(path.read_text())
        value["reviewed_snapshot_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        publication.verify_packages(root, packet, directory)


class CandidateProvider:
    """In-memory provider writes; package/archive auditing remains the real code."""

    def __init__(self, packet, directory, manifest):
        self.packet, self.directory, self.manifest = packet, directory, manifest
        self.release = {
            "id": 99,
            "tag_name": packet["tag"],
            "draft": True,
            "body": publication.marker(packet) + "\nFICTIONAL draft",
        }
        self.remote_assets, self.writes, self.verifications = [], [], []
        self.lose_ack = False
        self.persist = True
        self.fail_signature = False
        self.corrupt_asset = False
        self.private = False

    def read(self, argv, **kwargs):
        if argv[:3] == ["gh", "attestation", "verify"]:
            self.verifications.append(argv)
            if self.fail_signature:
                raise publication.gate.PreparationError("release_metadata_unavailable")
            return ""  # FICTIONAL successful verifier process, not real cryptography.
        if argv[:2] == ["git", "ls-files"]:
            return publication.gate.command(argv, **kwargs)
        assert argv[:2] == ["gh", "api"]
        if argv[-1].endswith("/releases?per_page=100"):
            return json.dumps([[self.release]])
        if argv[-1].endswith("/assets?per_page=100"):
            return json.dumps([self.remote_assets])
        name = argv[-1].rsplit("/", 1)[1]
        return json.dumps(
            {
                "name": name,
                "package_type": "container",
                "visibility": "private" if self.private else "public",
                "repository": {"full_name": publication.gate.REPOSITORY},
            }
        )

    def write(self, argv, root):
        self.writes.append(argv)
        assert "--clobber" not in argv
        if self.persist and argv[2] == "upload":
            paths = [Path(value) for value in argv[argv.index("--repo") + 2 :]]
            assert all(path.parent != self.directory for path in paths)
            self.remote_assets = [
                {
                    "id": number,
                    "name": path.name,
                    "state": "uploaded",
                    "size": path.stat().st_size,
                    "digest": "sha256:" + sha(path.read_bytes()),
                }
                for number, path in enumerate(paths, 100)
            ]
            if self.corrupt_asset:
                self.remote_assets[0]["digest"] = "sha256:" + "0" * 64
        elif self.persist and argv[2] == "edit":
            assert "--draft=false" in argv
            self.release.update(
                draft=False,
                prerelease=True,
                body=Path(argv[argv.index("--notes-file") + 1]).read_text(),
            )
        if self.lose_ack:
            raise publication.PublicationError("release_write_outcome_uncertain")


@pytest.fixture
def candidate(assets, real_packages, monkeypatch):
    _, _, directory, manifest = assets
    root, packet, packages = real_packages
    packet.update(reservation_id=99, prerelease=True)
    for key in ("source_revision", "source_snapshot_sha256"):
        manifest[key] = packet[key]
    for path in packages.iterdir():
        (directory / path.name).write_bytes(path.read_bytes())
    seal(directory, manifest)
    provider = CandidateProvider(packet, directory, manifest)
    actual = publication.verify_signatures

    def verifier(root, plan, directory, manifest, *, run):
        return actual(root, plan, directory, manifest, run=run, inspect=fake_registry(manifest))

    monkeypatch.setattr(publication, "verify_signatures", verifier)
    return root, packet, directory, manifest, provider


@pytest.mark.parametrize("lose_ack", [False, True])
def test_upload_then_publish_uses_captured_bytes_and_provider_readback(candidate, lose_ack):
    root, packet, directory, _, provider = candidate
    provider.lose_ack = lose_ack
    uploaded = publication.upload(root, packet, directory, run=provider.read, write=provider.write)
    assert uploaded["phase"] == "assets_uploaded" and uploaded["published"] is False
    result = publication.complete(root, packet, directory, run=provider.read, write=provider.write)
    assert result["published"] is True and result["provider_readback_verified"] is True
    assert result["write_acknowledged"] is (not lose_ack)
    assert [argv[2] for argv in provider.writes] == ["upload", "edit"]
    assert len(provider.verifications) == 32
    assert not list(root.parent.glob("release-assets-*"))
    assert not list(root.parent.glob("release-audit-*"))


@pytest.mark.parametrize(
    "fault",
    [
        "signature",
        "changed_archive",
        "private_image",
        "lost_upload",
        "false_digest",
        "lost_publish",
    ],
)
def test_failed_boundary_never_becomes_a_second_write_or_success(candidate, fault):
    root, packet, directory, manifest, provider = candidate
    if fault == "signature":
        provider.fail_signature = True
    elif fault == "changed_archive":
        make_packages(root, packet, directory, changed_module=True)
        seal(directory, manifest)
    elif fault == "lost_upload":
        provider.persist, provider.lose_ack = False, True
    elif fault == "false_digest":
        provider.corrupt_asset = True
    if fault in ("private_image", "lost_publish"):
        publication.upload(root, packet, directory, run=provider.read, write=provider.write)
        provider.writes.clear()
        if fault == "private_image":
            provider.private = True
        else:
            provider.persist, provider.lose_ack = False, True
        operation = publication.complete
    else:
        operation = publication.upload
    with pytest.raises(ValueError):
        operation(root, packet, directory, run=provider.read, write=provider.write)
    assert len(provider.writes) == (
        1 if fault in ("lost_upload", "false_digest", "lost_publish") else 0
    )
    assert provider.release["draft"] is True
    assert not list(root.parent.glob("release-assets-*"))


def test_verified_sbom_predicate_must_equal_the_distributed_file_even_with_successful_verifier(
    assets,
):
    root, packet, directory, manifest = assets
    path = directory / "aikey-amd64.cdx.json"
    bom = json.loads(path.read_text())
    bom["components"] = [
        {"type": "library", "name": "FICTIONAL-omitted-dependency", "version": "1.0"}
    ]
    path.write_text(json.dumps(bom))
    seal(directory, manifest)

    def accepted(argv, **kwargs):
        return "FICTIONAL successful process"

    with pytest.raises(ValueError, match="predicate_disagrees"):
        publication.verify_signatures(
            root, packet, directory, manifest, run=accepted, inspect=fake_registry(manifest)
        )


def test_untracked_wheel_startup_file_is_rejected_before_distribution_use(real_packages):
    root, packet, directory = real_packages
    wheel = next(directory.glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as package:
        package.writestr("unexpected.pth", "import fictional_unreviewed_startup\n")
    with pytest.raises(ValueError, match="non_audit_gate_blocked|module_set_mismatch"):
        publication.verify_packages(root, packet, directory)


def test_existing_exact_upload_is_reconciled_without_a_second_upload(candidate):
    root, packet, directory, _, provider = candidate
    publication.upload(root, packet, directory, run=provider.read, write=provider.write)
    provider.writes.clear()
    result = publication.upload(root, packet, directory, run=provider.read, write=provider.write)
    assert result["reconciled_existing"] is True and provider.writes == []
    provider.remote_assets.pop()
    with pytest.raises(ValueError, match="asset_set"):
        publication.upload(root, packet, directory, run=provider.read, write=provider.write)
    assert provider.writes == []

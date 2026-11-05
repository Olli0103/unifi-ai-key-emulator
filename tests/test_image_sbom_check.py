"""Invented image inventories exercise subject/format/finalization boundaries."""

import base64
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("image_sbom_check", TOOLS / "image_sbom_check.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
sys.path.pop(0)
REV, NONCE = "f" * 40, "123-1-arm64"


def inventory(profile="aikey"):
    labels = {
        "org.opencontainers.image.revision": REV,
        "org.local-aikey.install-run": NONCE,
        "org.local-aikey.install-profile": profile,
    }
    config = {"architecture": "arm64", "os": "linux", "config": {"Labels": labels}}
    raw = json.dumps(config).encode()
    image_id = "sha256:" + check.digest(raw)
    image = {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "arm64",
        "Config": {"User": "10001:10001", "Labels": labels},
    }
    identity = check.subject([image], REV, "arm64", NONCE, profile)
    packages = [
        ("local-aikey", "0.1.0", "python"),
        ("typing_extensions", "4.16.0", "python"),
        ("ffmpeg", "1.0-1", "deb"),
        ("openssl", "1.0-1", "deb"),
        ("ca-certificates", "1.0-1", "deb"),
    ]
    artifacts = [
        {
            "name": name,
            "version": version,
            "type": kind,
            "purl": f"pkg:fixture/{name}@{version}",
            "licenses": [],
        }
        for name, version, kind in packages
    ]
    native = {
        "source": {
            "type": "image",
            "name": image_id,
            "version": image_id,
            "metadata": {
                "imageID": image_id,
                "architecture": "arm64",
                "os": "linux",
                "labels": labels,
                "config": base64.b64encode(raw).decode(),
            },
        },
        "descriptor": {
            "name": "syft",
            "version": check.VERSION,
            "configuration": {"search": {"scope": "squashed"}, "licenses": {"include-content": "all"}},
        },
        "schema": {"version": "16.1.12"},
        "distro": {"id": "debian", "versionID": "12"},
        "artifacts": artifacts,
    }
    cdx = {
        "serialNumber": "urn:uuid:12345678-1234-4234-8234-123456789abc",
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "timestamp": "2026-11-04T00:00:00Z",
            "component": {"type": "container", "name": image_id, "version": image_id},
            "tools": {"components": [{"name": "syft", "version": check.VERSION}]},
        },
        "components": [
            dict(
                type="library",
                **{"bom-ref": str(index)},
                **{key: row[key] for key in ("name", "version", "purl")},
            )
            for index, row in enumerate(artifacts)
        ]
        + [
            {
                "bom-ref": "os:debian@12",
                "type": "operating-system",
                "name": "debian",
                "version": "12",
            }
        ],
    }
    expected = {"local-aikey": "0.1.0", "typing-extensions": "4.16.0"}
    return image, identity, native, cdx, expected, deepcopy(cdx)


def test_subject_bound_formats_retain_unknown_licenses_without_approval():
    _, identity, native, cdx, expected, converted = inventory()
    receipt = check.validate(native, cdx, identity, expected, converted=converted)
    assert receipt["package_count"] == 5 and receipt["packages_without_spdx_expression"] == 5
    assert receipt["runtime_lock_packages_verified"] == 2
    assert receipt["licenses_reviewed"] is False and receipt["release_approved"] is False
    assert receipt["package_types"] == {"deb": 3, "python": 2}


@pytest.mark.parametrize(
    "fault", ["id", "architecture", "os", "revision", "nonce", "profile", "user", "many"]
)
def test_foreign_inspection_cannot_reach_scanner(fault):
    image, *_ = inventory()
    if fault == "id":
        image["Id"] = "mutable:tag"
    if fault == "architecture":
        image["Architecture"] = "amd64"
    if fault == "os":
        image["Os"] = "windows"
    if fault in ("revision", "nonce", "profile"):
        label = {
            "revision": "org.opencontainers.image.revision",
            "nonce": "org.local-aikey.install-run",
            "profile": "org.local-aikey.install-profile",
        }[fault]
        image["Config"]["Labels"][label] = "foreign"
    if fault == "user":
        image["Config"]["User"] = "0:0"
    with pytest.raises(ValueError):
        check.subject([image, image] if fault == "many" else [image], REV, "arm64", NONCE, "aikey")


@pytest.mark.parametrize(
    "fault",
    [
        "image_id",
        "architecture",
        "os",
        "scan_revision",
        "scan_nonce",
        "scan_profile",
        "config_bytes",
        "config_labels",
        "config_arch",
        "scanner",
        "scanner_version",
        "scope",
        "schema",
        "cdx_version",
        "cdx_numeric_bool",
        "cdx_subject",
        "cdx_tool",
        "missing_component",
        "duplicate_component",
        "changed_version",
        "changed_purl",
        "missing_runtime",
        "wrong_runtime",
        "missing_ffmpeg",
        "new_type",
    ],
)
def test_stale_cross_image_or_incomplete_catalogue_is_not_a_release_inventory(fault):
    _, identity, native, cdx, expected, converted = inventory()
    metadata = native["source"]["metadata"]
    if fault == "image_id":
        metadata["imageID"] = "sha256:" + "a" * 64
    if fault == "architecture":
        metadata["architecture"] = "amd64"
    if fault == "os":
        metadata["os"] = "windows"
    if fault.startswith("scan_"):
        label = {
            "scan_revision": "org.opencontainers.image.revision",
            "scan_nonce": "org.local-aikey.install-run",
            "scan_profile": "org.local-aikey.install-profile",
        }[fault]
        metadata["labels"][label] = "foreign"
    if fault == "config_bytes":
        metadata["config"] = base64.b64encode(b"{}").decode()
    if fault in ("config_labels", "config_arch"):
        value = json.loads(base64.b64decode(metadata["config"]))
        if fault == "config_labels":
            value["config"]["Labels"] = {}
        else:
            value["architecture"] = "amd64"
        metadata["config"] = base64.b64encode(json.dumps(value).encode()).decode()
    if fault == "scanner":
        native["descriptor"]["name"] = "fictional-scanner"
    if fault == "scanner_version":
        native["descriptor"]["version"] = "0.0"
    if fault == "scope":
        native["descriptor"]["configuration"]["search"]["scope"] = "all-layers"
    if fault == "schema":
        native["schema"]["version"] = "0"
    if fault == "cdx_version":
        cdx["specVersion"] = "0"
    if fault == "cdx_numeric_bool":
        cdx["version"] = True
    if fault == "cdx_subject":
        cdx["metadata"]["component"]["version"] = "foreign"
    if fault == "cdx_tool":
        cdx["metadata"]["tools"]["components"] = []
    if fault == "missing_component":
        cdx["components"].pop()
    if fault == "duplicate_component":
        cdx["components"].append(deepcopy(cdx["components"][0]))
    if fault == "changed_version":
        cdx["components"][0]["version"] = "other"
    if fault == "changed_purl":
        cdx["components"][0]["purl"] = "pkg:fixture/other@1"
    if fault == "missing_runtime":
        expected["nonexistent"] = "1"
    if fault == "wrong_runtime":
        expected["typing-extensions"] = "0"
    if fault == "missing_ffmpeg":
        for rows in (native["artifacts"], cdx["components"]):
            rows[:] = [row for row in rows if row["name"] != "ffmpeg"]
    if fault == "new_type":
        native["artifacts"][0]["type"] = "unreviewed"
    with pytest.raises(ValueError):
        check.validate(native, cdx, identity, expected, converted=converted)


def saved_inventories(tmp_path):
    from test_container_install_check import complete_records

    complete_records(tmp_path)
    from container_install_check import summary

    # Existing fixture helper uses amd64; keep all producer/consumer identities coherent.
    for path in tmp_path.glob("*.json"):
        value = json.loads(path.read_text().replace("amd64", "arm64"))
        path.write_text(json.dumps(value))
    check.publish(tmp_path / "summary.json", summary(tmp_path, REV, "arm64"))
    for profile in ("aikey", "aiport"):
        _, identity, native, cdx, expected, converted = inventory(profile)
        path = tmp_path / (profile + "-sbom.cdx.json")
        path.write_text(json.dumps(cdx))
        value = check.validate(native, cdx, identity, expected, converted=converted) | {
            "sbom_sha256": check.digest(path.read_bytes())
        }
        review = check.license_review(native, identity, native_sha256=check.digest(json.dumps(native).encode()),
                                      sbom_sha256=value['sbom_sha256'])
        review_path = tmp_path / (profile + '-licenses.json')
        check.publish(review_path, review)
        value.update(license_review_sha256=check.digest(review_path.read_bytes()),
                     license_texts_retained=0, license_text_bytes=0)
        check.publish(tmp_path / (profile + "-inventory.json"), value)


def test_finalization_requires_both_subjects_install_phases_and_cleanup(tmp_path):
    saved_inventories(tmp_path)
    result = check.finalize(tmp_path, REV, "arm64", NONCE)
    assert result["cleanup_verified"] is True and result["install_cases_finished"] == 8
    assert result["release_approved"] is False and len(result["profiles"]) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing_profile",
        "stale_nonce",
        "stale_revision",
        "swapped_profile",
        "changed_sbom",
        "missing_licenses",
        "changed_licenses",
        "extra_field",
        "approval",
        "bool_count",
        "count_mismatch",
        "unknown_count",
        "missing_phase",
        "missing_cleanup",
        "failed_cleanup",
        "same_image",
    ],
)
def test_finalization_never_accepts_partial_stale_or_changed_evidence(tmp_path, fault):
    saved_inventories(tmp_path)
    path = tmp_path / "aiport-inventory.json"
    value = json.loads(path.read_text())
    if fault == "stale_nonce":
        value["nonce"] = "123-2-arm64"
    if fault == "stale_revision":
        value["revision"] = "a" * 40
    if fault == "swapped_profile":
        value["profile"] = "aikey"
    if fault == "extra_field":
        value["private_payload"] = "fictional-canary"
    if fault == "approval":
        value["release_approved"] = True
    if fault == "bool_count":
        value["package_count"] = True
    if fault == "count_mismatch":
        value["package_count"] = 9999
    if fault == "unknown_count":
        value["packages_without_spdx_expression"] = -1
    if fault == "same_image":
        value["image_id"] = check.load(tmp_path / "aikey-inventory.json")["image_id"]
    path.write_text(json.dumps(value))
    if fault == "missing_profile":
        path.unlink()
    if fault == "changed_sbom":
        (tmp_path / "aiport-sbom.cdx.json").write_text("{}")
    if fault == "missing_licenses":
        (tmp_path / "aiport-licenses.json").unlink()
    if fault == "changed_licenses":
        (tmp_path / "aiport-licenses.json").write_text('{}')
    if fault == "missing_phase":
        (tmp_path / "aikey-reopen.json").unlink()
    if fault == "missing_cleanup":
        (tmp_path / "cleanup.json").unlink()
    if fault == "failed_cleanup":
        cleanup = check.load(tmp_path / "cleanup.json")
        cleanup["containers"] = 1
        (tmp_path / "cleanup.json").write_text(json.dumps(cleanup))
    with pytest.raises((ValueError, OSError)):
        check.finalize(tmp_path, REV, "arm64", NONCE)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_bounded_json_contract_refuses_ambiguity(raw):
    with pytest.raises(ValueError):
        check.json_bytes(raw)


def test_actual_cli_from_unrelated_cwd_has_no_receipt_on_missing_evidence(tmp_path):
    output = tmp_path / "must-not-exist.json"
    result = subprocess.run(
        [
            sys.executable,
            str(TOOLS / "image_sbom_check.py"),
            "finalize",
            "--architecture",
            "arm64",
            "--revision",
            REV,
            "--nonce",
            NONCE,
            "--results",
            str(tmp_path),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2 and not output.exists() and not result.stderr
    assert b"release_approved" in result.stdout and str(tmp_path).encode() not in result.stdout


def test_checksum_failure_never_extracts_or_installs_a_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        check.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"wrong archive")
    )
    with pytest.raises(ValueError):
        check.install(tmp_path / "install", "arm64")
    assert not (tmp_path / "install/syft").exists()


@pytest.mark.parametrize("member_type", ["regular", "symlink", "duplicate"])
def test_install_uses_one_verified_archive_and_only_regular_exact_binary(
    tmp_path, monkeypatch, member_type
):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        item = tarfile.TarInfo("syft")
        payload = b"fictional tool, not executable vendor code"
        if member_type == "symlink":
            item.type = tarfile.SYMTYPE
            item.linkname = "/fictional-canary"
        else:
            item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload) if item.isreg() else None)
        if member_type == "duplicate":
            archive.addfile(item, io.BytesIO(payload))
        bad = tarfile.TarInfo("../../must-not-extract")
        bad.size = 1
        archive.addfile(bad, io.BytesIO(b"x"))
    raw = buffer.getvalue()
    monkeypatch.setitem(check.ARCHIVES, "arm64", check.digest(raw))
    monkeypatch.setattr(check.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(raw))
    if member_type == "regular":
        check.install(tmp_path / "install", "arm64")
        assert (tmp_path / "install/syft").read_bytes() == payload
        assert (tmp_path / "install/syft").stat().st_mode & 0o777 == 0o700
    else:
        with pytest.raises(ValueError):
            check.install(tmp_path / "install", "arm64")
        assert not (tmp_path / "install/syft").exists()
    assert not (tmp_path / "must-not-extract").exists()


def test_child_exit_and_deadline_are_not_inventory_success():
    with pytest.raises(ValueError):
        check.command([sys.executable, "-c", "raise SystemExit(7)"])
    with pytest.raises(subprocess.TimeoutExpired):
        check.command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)


def test_exact_locks_include_build_backend_and_local_distribution():
    expected = check.expected_python(TOOLS.parent)
    assert expected["local-aikey"] == "0.1.0" and expected["setuptools"] == "84.0.0"
    assert expected["typing-extensions"] == "4.16.0" and len(expected) == 18


def test_license_metadata_must_agree_without_turning_it_into_legal_approval():
    _, identity, native, cdx, expected, converted = inventory()
    native["artifacts"][0]["licenses"] = [
        {"spdxExpression": "Apache-2.0", "value": "Apache License"}
    ]
    cdx["components"][0]["licenses"] = [{"license": {"id": "Apache-2.0"}}]
    converted = deepcopy(cdx)
    assert (
        check.validate(native, cdx, identity, expected, converted=converted)["licenses_reviewed"]
        is False
    )
    cdx["components"][0]["licenses"] = [{"license": {"id": "MIT"}}]
    with pytest.raises(ValueError, match="conversion_disagrees"):
        check.validate(native, cdx, identity, expected, converted=converted)


def test_license_attachment_retains_deduplicated_original_bytes_without_native_private_fields(tmp_path):
    _, identity, native, _, _, _ = inventory()
    text = 'Fictional license notice.\nRetain exact newlines and ünicode.\n'
    sha = check.digest(text.encode())
    native['source']['metadata']['private-canary'] = 'must-not-be-published'
    item = {'value': 'sha256:' + sha, 'spdxExpression': '', 'type': 'concluded', 'contents': text}
    native['artifacts'][0]['licenses'] = [item]
    native['artifacts'][1]['licenses'] = [dict(item)]
    review = check.license_review(native, identity, native_sha256='a' * 64, sbom_sha256='b' * 64)
    assert review['texts'] == {sha: text}
    assert review['packages'][2]['licenses'] == []  # Missing is not approval or a fabricated license.
    assert 'must-not-be-published' not in json.dumps(review)
    assert not review['licenses_reviewed'] and not review['release_approved']
    path = tmp_path / 'review.json'
    check.publish(path, review)
    receipt = dict(identity, sbom_sha256='b' * 64, package_count=5,
                   license_review_sha256=check.digest(path.read_bytes()),
                   license_texts_retained=1, license_text_bytes=len(text.encode()))
    assert check.read_license_review(path, receipt) == review
    review['texts'][sha] += 'changed'
    path.write_text(json.dumps(review))
    receipt['license_review_sha256'] = check.digest(path.read_bytes())
    with pytest.raises(ValueError, match='saved_license_review_refused'):
        check.read_license_review(path, receipt)


@pytest.mark.parametrize('content', [None, '', 'different content'])
def test_hash_named_license_cannot_be_retained_without_the_exact_text(content):
    _, identity, native, _, _, _ = inventory()
    native['artifacts'][0]['licenses'] = [{'value': 'sha256:' + check.digest(b'original'),
                                           'spdxExpression': '', 'type': 'concluded', 'contents': content}]
    with pytest.raises(ValueError):
        check.license_review(native, identity, native_sha256='a' * 64, sbom_sha256='b' * 64)


def test_large_license_catalogue_is_not_limited_to_small_control_receipts(tmp_path):
    raw = json.dumps({'fictional_notice': 'x' * (300 * 1024)}).encode()
    path = tmp_path / 'licenses.json'
    check.publish_catalog(path, raw)
    assert path.read_bytes() == raw and path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        check.publish_catalog(path, b'{}')
    with pytest.raises(ValueError, match='publication_too_large'):
        check.publish_catalog(tmp_path / 'oversized.json', b'x' * (check.LIMIT + 1))
    assert not (tmp_path / 'oversized.json').exists()


@pytest.mark.parametrize(
    "fault", ["none", "changed_file", "foreign_file", "wrong_os", "unknown_type"]
)
def test_nonpackage_components_match_their_real_native_producer(fault):
    _, identity, native, cdx, expected, converted = inventory()
    native["files"] = [
        {
            "id": "file-ref",
            "location": {"path": "/usr/lib/synthetic-fixture"},
            "digests": [{"algorithm": "sha256", "value": "a" * 64}],
        }
    ]
    component = {
        "bom-ref": "file-ref",
        "type": "file",
        "name": "/usr/lib/synthetic-fixture",
        "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
    }
    cdx["components"].append(component)
    converted = deepcopy(cdx)
    if fault == "changed_file":
        component["hashes"][0]["content"] = "b" * 64
    if fault == "foreign_file":
        component["bom-ref"] = "foreign"
    if fault == "wrong_os":
        cdx["components"][-2]["version"] = "other"
    if fault == "unknown_type":
        component["type"] = "unknown"
    if fault == "none":
        assert (
            check.validate(native, cdx, identity, expected, converted=converted)["package_count"]
            == 5
        )
    else:
        with pytest.raises(ValueError):
            check.validate(native, cdx, identity, expected, converted=converted)


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "foreign_image",
        "scanner_exit",
        "changed_tool",
        "partial_output",
        "converted_changed",
        "license_content_disabled",
        "license_text_missing",
    ],
)
def test_actual_collect_entrypoint_with_owned_fake_daemon_and_scanner(tmp_path, fault):
    image, _, native, cdx, _, _ = inventory()
    canary = "synthetic-private-native-report-canary"
    native["private_fixture_canary"] = canary
    if fault == 'license_content_disabled':
        native['descriptor']['configuration']['licenses']['include-content'] = 'none'
    if fault == 'license_text_missing':
        native['artifacts'][0]['licenses'] = [{'value': 'sha256:' + 'a' * 64,
                                              'type': 'concluded', 'spdxExpression': ''}]
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nversion="0.1.0"\n')
    (source / "requirements-runtime.lock").write_text("typing_extensions==4.16.0\n")
    (source / "requirements-build.lock").write_text("# synthetic empty build lock\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    if fault == "foreign_image":
        image["Config"]["Labels"]["org.local-aikey.install-run"] = "foreign"
    docker = binaries / "docker"
    docker.write_text("#!" + sys.executable + "\nprint(" + repr(json.dumps([image])) + ")\n")
    docker.chmod(0o700)
    native_file, cdx_file = tmp_path / "native.json", tmp_path / "cdx.json"
    native_file.write_text(json.dumps(native))
    cdx_file.write_text(json.dumps(cdx))
    trace = tmp_path / "scanner-called"
    scanner = binaries / "syft"
    scanner.write_text(
        "#!"
        + sys.executable
        + "\n"
        + "import json,sys,pathlib\n"
        + "pathlib.Path("
        + repr(str(trace))
        + ').write_text("called")\n'
        + 'if sys.argv[1]=="version":\n print('
        + repr(json.dumps({"version": check.VERSION, "gitCommit": check.SYFT_COMMIT}))
        + ")\n sys.exit(0)\n"
        + ("print(" + repr(canary) + ");sys.exit(7)\n" if fault == "scanner_exit" else "")
        + 'if sys.argv[1]=="scan": assert sys.argv[-1]=='
        + repr("docker:" + image["Id"])
        + "\n"
        + "for argument in sys.argv:\n"
        + ' if argument.startswith("syft-json="):\n  pathlib.Path(argument.split("=",1)[1]).write_bytes(pathlib.Path('
        + repr(str(native_file))
        + ").read_bytes())\n"
        + (
            ' if argument.startswith("cyclonedx-json="):\n  pathlib.Path(argument.split("=",1)[1]).write_bytes(pathlib.Path('
            + repr(str(cdx_file))
            + ").read_bytes())\n"
            if fault != "partial_output"
            else ""
        )
    )
    scanner.chmod(0o700)
    if fault == "converted_changed":
        with scanner.open("a") as output:
            output.write(
                '\nif sys.argv[1]=="convert":\n'
                ' target=pathlib.Path(sys.argv[-1].split("=",1)[1])\n'
                " value=json.loads(target.read_text())\n"
                ' value["components"][0]["licenses"]=[{"license":{"id":"MIT"}}]\n'
                " target.write_text(json.dumps(value))\n"
            )
    check.publish(
        binaries / "tool.json",
        {
            "version": check.VERSION,
            "architecture": "arm64",
            "archive_sha256": check.ARCHIVES["arm64"],
            "binary_sha256": check.digest(scanner.read_bytes()),
        },
    )
    if fault == "changed_tool":
        scanner.write_bytes(scanner.read_bytes() + b"\n# changed after verified install\n")
    results = tmp_path / "results"
    results.mkdir()
    args = [
        sys.executable,
        str(TOOLS / "image_sbom_check.py"),
        "collect",
        "--source",
        str(source),
        "--tool",
        str(scanner),
        "--profile",
        "aikey",
        "--repository",
        "example/project",
        "--architecture",
        "arm64",
        "--revision",
        REV,
        "--nonce",
        NONCE,
        "--results",
        str(results),
    ]
    outcome = subprocess.run(
        args,
        cwd=tmp_path,
        env={"PATH": str(binaries) + os.pathsep + os.defpath},
        capture_output=True,
        timeout=15,
    )
    assert canary.encode() not in outcome.stdout + outcome.stderr and not outcome.stderr
    receipt = results / "aikey-inventory.json"
    if fault == "none":
        assert outcome.returncode == 0 and receipt.exists()
        assert canary not in (results / "aikey-sbom.cdx.json").read_text() + receipt.read_text()
        saved = check.load(receipt)
        assert saved["sbom_sha256"] == check.digest((results / "aikey-sbom.cdx.json").read_bytes())
        review_path = results / 'aikey-licenses.json'
        assert canary not in review_path.read_text()
        assert check.read_license_review(review_path, saved)['licenses_reviewed'] is False
    else:
        assert outcome.returncode == 2 and not receipt.exists() and not list(results.iterdir())
    if fault in ("foreign_image", "changed_tool"):
        assert not trace.exists()


@pytest.mark.parametrize("native_value", ["", None])
def test_optional_unknown_purl_is_retained_counted_and_not_fabricated(native_value):
    _, identity, native, cdx, expected, converted = inventory()
    native["artifacts"][0]["purl"] = native_value
    cdx["components"][0].pop("purl")
    converted["components"][0].pop("purl")
    result = check.validate(native, cdx, identity, expected, converted=converted)
    assert result["package_count"] == 5 and result["packages_without_purl"] == 1
    assert result["runtime_lock_packages_verified"] == 2
    assert result["licenses_reviewed"] is False and result["release_approved"] is False
    assert "purl" not in cdx["components"][0]  # No mutation to manufacture metadata.


@pytest.mark.parametrize("value", [False, 12, [], {}, "not-a-package-url", "pkg:" + "x" * 4096])
def test_optional_purl_is_not_permission_for_malformed_metadata(value):
    _, identity, native, cdx, expected, converted = inventory()
    native["artifacts"][0]["purl"] = value
    cdx["components"][0]["purl"] = value
    converted["components"][0]["purl"] = value
    with pytest.raises(ValueError, match="component_identity_refused"):
        check.validate(native, cdx, identity, expected, converted=converted)


def test_unknown_purl_cannot_match_a_different_present_purl():
    _, identity, native, cdx, expected, converted = inventory()
    native["artifacts"][0]["purl"] = ""
    with pytest.raises(ValueError, match="formats_disagree"):
        check.validate(native, cdx, identity, expected, converted=converted)


def test_only_new_valid_serial_and_aware_timestamp_are_volatile_conversion_fields():
    _, _, _, cdx, _, converted = inventory()
    converted["serialNumber"] = "urn:uuid:87654321-1234-4234-8234-123456789abc"
    converted["metadata"]["timestamp"] = "2026-11-04T01:02:03+01:00"
    check.conversion_equal(cdx, converted)
    assert cdx["metadata"]["timestamp"] == "2026-11-04T00:00:00Z"  # No input mutation.


@pytest.mark.parametrize(
    "fault", ["serial", "timestamp", "naive_time", "extra_field", "license", "dependency"]
)
def test_full_conversion_comparison_rejects_other_changes(fault):
    _, _, _, cdx, _, converted = inventory()
    if fault == "serial":
        converted["serialNumber"] = "fictional-invalid-serial"
    if fault == "timestamp":
        converted["metadata"]["timestamp"] = "fictional-invalid-time"
    if fault == "naive_time":
        converted["metadata"]["timestamp"] = "2026-11-04T00:00:00"
    if fault == "extra_field":
        converted["unreviewed-field"] = "fictional-canary"
    if fault == "license":
        converted["components"][0]["licenses"] = [{"license": {"id": "MIT"}}]
    if fault == "dependency":
        converted["dependencies"] = [{"ref": "foreign-reference"}]
    with pytest.raises(ValueError):
        check.conversion_equal(cdx, converted)

"""Integrity and honesty rules for the versioned AI Key compatibility manifest.

Regenerate the Markdown matrix after editing the manifest:
    python tests/test_compatibility_manifest.py --write
"""

import json
from pathlib import Path
import re
import sys

import pytest

from aikey.protocol import (COMPATIBILITY_MANIFEST_VERSION, CONTROLLER_VERSION_EVIDENCE,
                            classify_controller_version)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "evidence" / "compatibility-manifest.json"
MATRIX = ROOT / "docs" / "evidence" / "compatibility-matrix.md"
FIXTURES = ROOT / "tests" / "fixtures" / "compatibility"
STATUSES = {"native-verified", "fixture-tested", "implemented", "unsupported", "needs_evidence"}
LIVE_RESULTS = {"native-verified", "indirect", "not_observed"}
ACTIVATIONS = {"default", "explicit_opt_in", "experimental_opt_in"}


def load(path=MANIFEST):
    return json.loads(path.read_text())


def sources_by_id(manifest):
    return {source["id"]: source for source in manifest["evidence_sources"]}


def test_manifest_header_matches_runtime_constants():
    manifest = load()
    assert manifest["schema"] == "aikey-compatibility-manifest/1"
    assert manifest["manifest_version"] == COMPATIBILITY_MANIFEST_VERSION
    assert set(manifest["statuses"]) == STATUSES
    assert set(manifest["live_results"]) == LIVE_RESULTS
    expected = {}
    for source in manifest["evidence_sources"]:
        if source["kind"] == "live":
            expected[source["protect_version"]] = "live_partial"
        elif source["kind"] == "static" and "protect_version" in source:
            expected[source["protect_version"]] = "static_only"
    assert CONTROLLER_VERSION_EVIDENCE == expected


def test_evidence_sources_keep_live_static_and_synthetic_separate():
    manifest = load()
    sources = sources_by_id(manifest)
    assert len(sources) == len(manifest["evidence_sources"])
    kinds = {source["kind"] for source in sources.values()}
    assert kinds == {"live", "static", "vendor_documentation", "synthetic"}
    for source in sources.values():
        for record in source["records"]:
            assert (ROOT / record).is_file(), (source["id"], record)
        if source["kind"] == "live":
            assert source["observed"] and source["not_observed"]
            assert source["raw_traces_public"] is False
        if source["kind"] == "static":
            assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in source["artifacts"])
    live_versions = {s["protect_version"] for s in sources.values() if s["kind"] == "live"}
    static_versions = {s.get("protect_version") for s in sources.values() if s["kind"] == "static"}
    assert live_versions.isdisjoint(static_versions)


def _test_exists(node):
    path, *names = node.split("::")
    source = (ROOT / path).read_text()
    assert names, node
    if len(names) == 2:
        assert re.search(rf"^class {re.escape(names[0])}\b", source, re.M), node
    assert re.search(rf"^\s*(async )?def {re.escape(names[-1])}\(", source, re.M), node


def test_every_feature_obeys_status_rules():
    manifest = load()
    sources = sources_by_id(manifest)
    ids = [feature["id"] for feature in manifest["features"]]
    assert len(ids) == len(set(ids))
    for feature in manifest["features"]:
        name = feature["id"]
        assert re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", name), name
        assert feature["status"] in STATUSES, name
        assert feature["activation"] in ACTIVATIONS, name
        for source_id, result in feature["live"].items():
            assert sources[source_id]["kind"] == "live", (name, source_id)
            assert result in LIVE_RESULTS, name
        for source_id in feature["static"]:
            assert sources[source_id]["kind"] in {"static", "vendor_documentation"}, (name, source_id)
        for node in feature["tests"]:
            _test_exists(node)
        for path in feature["implementation"]:
            assert (ROOT / path).is_file(), (name, path)
        verified = [s for s, result in feature["live"].items() if result == "native-verified"]
        status = feature["status"]
        # A live observation is the only route to native-verified, and it cannot be understated.
        assert (status == "native-verified") == bool(verified), name
        if status in {"native-verified", "fixture-tested"}:
            assert feature["tests"], name
        if status == "unsupported":
            assert feature.get("rejection"), name
        if status == "needs_evidence":
            assert feature["missing_evidence"], name


def test_capability_flags_and_http_200_are_never_the_only_evidence():
    manifest = load()
    for feature in manifest["features"]:
        if feature["area"] == "capabilities" and feature["status"] != "unsupported":
            assert feature["status"] != "native-verified", feature["id"]
    caption = next(f for f in manifest["features"] if f["id"] == "callback.ram_full_event_tagging")
    assert "reload" in caption["notes"] and "not by HTTP 200" in caption["notes"]


def test_contradictions_and_native_tests_reference_known_items():
    manifest = load()
    sources = sources_by_id(manifest)
    features = {feature["id"] for feature in manifest["features"]}
    ids = [item["id"] for item in manifest["contradictions"]]
    assert len(ids) == len(set(ids))
    for item in manifest["contradictions"]:
        assert item["statement"] and item["resolution"]
        for source in item["sources"]:
            assert source in sources or (ROOT / source).is_file(), (item["id"], source)
    for test in manifest["remaining_native_tests"]:
        assert set(test["features"]) <= features, test["id"]
    covered = {name for test in manifest["remaining_native_tests"] for name in test["features"]}
    for feature in manifest["features"]:
        if feature["id"] == "lifecycle.controller_upgrade":
            assert feature["id"] in covered
    for entry in manifest["trial_activation"]["recorded"] + manifest["trial_activation"]["not_recorded"]:
        assert sources[entry["source"]]["kind"] == "live"


def test_fixture_features_exist_and_ids_are_unique():
    features = {feature["id"] for feature in load()["features"]}
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text())
        assert fixture["manifest"] == COMPATIBILITY_MANIFEST_VERSION
        assert fixture["provenance"]["kind"] == "synthetic"
        assert fixture["provenance"]["captured_from_device"] is False
        ids = []
        for profile in fixture["profiles"].values():
            for exchange in profile["exchanges"]:
                assert exchange["feature"] in features, exchange["id"]
                ids.append(exchange["id"])
        for case in fixture["malformed_frames"]:
            assert case["feature"] in features, case["id"]
            ids.append(case["id"])
        assert len(ids) == len(set(ids))


_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ALLOWED_IP = re.compile(r"^(127\.0\.0\.1|192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+)$")
_FORBIDDEN = [re.compile(p, re.I) for p in (
    r"BEGIN [A-Z ]*PRIVATE KEY", r"\bBearer\s", r"sk-[A-Za-z0-9]{8}", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}",
    r"\.ts\.net\b", r"/Users/", r"\b[0-9a-f]{24}\b", r"\bset-cookie\b", r"\bTOKEN=")]


def _strings(value, key=None):
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _strings(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, key)
    elif isinstance(value, str):
        yield key, value


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")) + [MANIFEST], ids=lambda p: p.name)
def test_public_artifacts_are_sanitized(path):
    text = path.read_text()
    for pattern in _FORBIDDEN:
        assert not pattern.search(text), (path.name, pattern.pattern)
    for mac in _MAC.findall(text):
        # Only locally administered, unicast test identities.
        assert int(mac[:2], 16) & 0b11 == 0b10, mac
    for address in _IPV4.findall(text):
        if address.startswith("0."):
            continue  # 0.0.0.0/8 is not a host address; this matches package versions like 0.1.47.1.
        assert _ALLOWED_IP.match(address), address
    if path.parent == FIXTURES:
        for key, value in _strings(json.loads(text)):
            if key and re.search(r"token|password|passwordOld|passwordNew", key, re.I):
                assert value == "ui" or value.startswith("synthetic"), key


def test_controller_version_classification_is_fixed_and_never_echoes_input():
    assert classify_controller_version("7.3.60") == ("7.3.60", "live_partial")
    assert classify_controller_version("7.3.56") == ("7.3.56", "live_partial")
    assert classify_controller_version("7.2.105") == ("7.2.105", "static_only")
    assert classify_controller_version(None) == (None, "not_reported")
    assert classify_controller_version("99.0.0") == (None, "unknown")
    for value in ("7.3.60 ", "7.3.60-beta.1", "<script>", "", 7.3, ["7.3.60"], "1" * 5 + ".0"):
        assert classify_controller_version(value) == (None, "unrecognized_format"), value


def render_matrix(manifest):
    sources = sources_by_id(manifest)
    live_ids = [s["id"] for s in manifest["evidence_sources"] if s["kind"] == "live"]
    lines = [
        "# AI Key compatibility matrix",
        "",
        f"<!-- Generated from compatibility-manifest.json ({manifest['manifest_version']}). "
        "Run `python tests/test_compatibility_manifest.py --write`; do not edit by hand. -->",
        "",
        f"Manifest `{manifest['manifest_version']}` for the `{manifest['processor_profile']}` profile, "
        f"based on commit `{manifest['base_commit']}`. {manifest['scope']}",
        "",
        *(f"- `{k}`: {v}" for k, v in manifest["statuses"].items()),
        "",
        "Live columns show each live trial separately. `indirect` means the behavior was necessarily "
        "exercised by another verified workflow but not individually recorded. Static references are "
        "source inspection of other versions and never count as native evidence.",
        "",
        "| Feature | Status | " + " | ".join(f"Live {sources[i]['protect_version']}" for i in live_ids)
        + " | Static references | Activation |",
        "| --- | --- | " + " | ".join("---" for _ in live_ids) + " | --- | --- |",
    ]
    static_label = {s["id"]: s.get("protect_version") and f"Protect {s['protect_version']}"
                    or s.get("firmware_version") and f"AI Key {s['firmware_version']}" or "vendor docs"
                    for s in manifest["evidence_sources"]}
    area = None
    for feature in manifest["features"]:
        if feature["area"] != area:
            area = feature["area"]
            lines.append(f"| **{area}** | | " + " | ".join("" for _ in live_ids) + " | | |")
        lives = " | ".join(feature["live"].get(i, "—") for i in live_ids)
        statics = ", ".join(static_label[s] for s in feature["static"]) or "—"
        lines.append(f"| `{feature['id']}`: {feature['title']} | {feature['status']} | {lives} | "
                     f"{statics} | {feature['activation']} |")
    lines += ["", "## Missing evidence", ""]
    for feature in manifest["features"]:
        for item in feature["missing_evidence"]:
            marker = " (needs_evidence)" if feature["status"] == "needs_evidence" else ""
            lines.append(f"- `{feature['id']}`{marker}: {item}")
    lines += ["", "## Contradictions and stale records", ""]
    for item in manifest["contradictions"]:
        lines.append(f"- **{item['id']}** ({item['type']}): {item['statement']} Resolution: {item['resolution']}")
    activation = manifest["trial_activation"]
    lines += ["", "## Experimental activation during live trials", "", activation["rule"], ""]
    for entry in activation["recorded"]:
        lines.append(f"- `{entry['source']}`: `{entry['activation']}`: {entry['state']}.")
    for entry in activation["not_recorded"]:
        lines.append(f"- `{entry['source']}`: `{entry['activation']}`: not in public records ({entry['state']}).")
    lines += ["", "## Remaining native tests for the integration owner", ""]
    for test in manifest["remaining_native_tests"]:
        lines.append(f"- **{test['id']}** ({', '.join(f'`{f}`' for f in test['features'])}): {test['procedure']}")
    return "\n".join(lines) + "\n"


def test_markdown_matrix_is_generated_from_manifest():
    assert MATRIX.read_text() == render_matrix(load()), "Run: python tests/test_compatibility_manifest.py --write"


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit("usage: python tests/test_compatibility_manifest.py --write")
    MATRIX.write_text(render_matrix(load()))

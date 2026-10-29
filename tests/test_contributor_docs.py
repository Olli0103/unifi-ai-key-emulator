"""Contributor templates collect sanitized facts and demand native evidence (#11)."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
FORMS = sorted(p for p in TEMPLATES.glob("*.yml") if p.name != "config.yml")


def _load(path):
    return yaml.safe_load(path.read_text())


def _block(markdown, heading):
    """The first shell block under a heading."""
    section = markdown.split(heading, 1)[1]
    return re.search(r"```sh\n(.*?)```", section, re.S).group(1).strip().splitlines()


def test_every_form_warns_and_requires_a_privacy_check():
    assert {p.name for p in FORMS} == {"bug_report.yml", "compatibility_report.yml", "feature_request.yml"}
    for path in FORMS:
        form = _load(path)
        text = path.read_text().lower()
        assert "credentials" in text and ("media" in text or "images" in text), path.name
        privacy = [item for item in form["body"] if item.get("id") == "privacy"]
        assert privacy and privacy[0]["type"] == "checkboxes"
        assert all(option["required"] is True for option in privacy[0]["attributes"]["options"])
        # No field invites uploads or secrets.
        for item in form["body"]:
            label = str(item.get("attributes", {}).get("label", "")).lower()
            assert item["type"] in {"markdown", "input", "textarea", "dropdown", "checkboxes"}
            assert not re.search(r"\b(api key|password|token|screenshot|recording|upload)\b", label), label


def test_parity_reports_must_state_native_or_weaker_evidence():
    form = _load(TEMPLATES / "compatibility_report.yml")
    fields = {item.get("id"): item for item in form["body"]}
    evidence = fields["evidence"]
    assert evidence["type"] == "dropdown" and evidence["validations"]["required"] is True
    options = " | ".join(evidence["attributes"]["options"]).lower()
    assert "native-verified" in options and "not native evidence" in options
    assert fields["readback"]["validations"]["required"] is True
    assert fields["protect"]["validations"]["required"] is True
    bug = {item.get("id"): item for item in _load(TEMPLATES / "bug_report.yml")["body"]}
    assert bug["commit"]["validations"]["required"] and bug["protect"]["validations"]["required"]
    assert "remove ids, hostnames" in bug["diagnostics"]["attributes"]["description"].lower()


def test_blank_issues_are_off_and_security_goes_to_security_md():
    config = _load(TEMPLATES / "config.yml")
    assert config["blank_issues_enabled"] is False
    [link] = config["contact_links"]
    assert link["url"].endswith("/SECURITY.md") and (ROOT / "SECURITY.md").is_file()
    assert "public issue" in link["about"].lower()


def test_the_pr_template_separates_evidence_levels():
    template = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    for level in ("**Implemented**", "**Fixture-tested**", "**Native-verified**", "## needs_evidence"):
        assert level in template
    assert "fails without this change" in template


def test_contributing_matches_the_readme_lab_and_ci():
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    readme = (ROOT / "README.md").read_text()
    ours = _block(contributing, "## Run the lab and checks from a clean checkout")
    lab = _block(readme, "## Run the local lab")
    assert [line for line in lab if line in ours] == lab            # every README step, in order
    workflows = "".join(p.read_text() for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert "ruff check ." in workflows and "pytest -q" in workflows
    assert ".venv/bin/python -m ruff check ." in ours and ".venv/bin/python -m pytest -q" in ours
    for rule in ("native-verified", "needs_evidence", "SECURITY.md", "assistant"):
        assert rule in contributing

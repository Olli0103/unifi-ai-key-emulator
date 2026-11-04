"""The public CI security gate must retain its least-privilege properties."""

from pathlib import Path
import re

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/security.yml"
WORKFLOWS = sorted(WORKFLOW.parent.glob('*.yml'))


@pytest.mark.parametrize('path',WORKFLOWS,ids=lambda path:path.name)
def test_security_workflow_pins_actions_and_uses_read_only_permissions(path):
    workflow = path.read_text()
    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    assert "persist-credentials: false" in workflow
    assert not re.search(r'^\s*(contents|packages|id-token):\s*write\s*$',workflow,re.M)
    actions = re.findall(r"uses: ([^\s#]+)", workflow)
    assert actions
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)


def test_security_workflow_keeps_required_release_gates():
    workflow = WORKFLOW.read_text()
    for required in (
        "python -m pytest -q", "python -m ruff check .", "python -m build",
        "pip-audit==2.10.1", "cyclonedx-bom==7.4.0", "python -m pip_audit",
        "python -m venv .runtime-sbom", "cyclonedx_py environment .runtime-sbom",
        "--no-deps -r requirements-runtime.lock", ".runtime-sbom/bin/python -m pip check",
        "sbom.cdx.json", "fetch-depth: 0", "GITLEAKS_VERSION: 8.30.1",
        "GITLEAKS_ARCHIVE_SHA256:", "sha256sum --check --strict",
        "gitleaks\" git --redact --no-banner --exit-code 1 .",
    ):
        assert required in workflow
    assert "GITHUB_TOKEN" not in workflow
    assert "gitleaks/gitleaks-action" not in workflow

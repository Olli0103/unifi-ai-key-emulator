"""Least privilege for untrusted CI; explicit owner-only release capability graph.

This is a structural regression guard, not a human approval or proof of live
GitHub protection rules. The release preparation gate checks those separately.
"""

from copy import deepcopy
from pathlib import Path
import re

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/security.yml"
WORKFLOWS = sorted(WORKFLOW.parent.glob("*.yml"))
PUBLISHER = WORKFLOW.parent / "publish-release.yml"
OWNER_DISPATCH = (
    "github.repository == 'Olli0103/unifi-ai-key-emulator' && "
    "github.ref == 'refs/heads/main' && github.actor == 'Olli0103' && "
    "github.triggering_actor == 'Olli0103' && github.run_attempt == 1"
)


def workflow_policy(value, *, publisher=False):
    assert value["permissions"] == {"contents": "read"}
    jobs = value["jobs"]
    assert isinstance(jobs, dict) and jobs
    if not publisher:
        for job in jobs.values():
            permissions = job.get("permissions", {})
            assert isinstance(permissions, dict)
            assert all(scope in ("read", "none") for scope in permissions.values())
        return
    triggers = value["on"]
    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"tag", "publish"}
    assert inputs["publish"]["type"] == "boolean" and inputs["publish"]["default"] is False
    assert inputs["tag"]["type"] == "string" and inputs["tag"]["required"] is True
    assert value["concurrency"] == {"group": "owner-gated-release", "cancel-in-progress": False}
    expected = {
        "qualify": ({"contents": "read", "actions": "read"}, None, None),
        "reserve": ({"contents": "write", "actions": "read"}, "qualify", "release-stage"),
        "packages": (
            {"contents": "read", "actions": "read", "id-token": "write", "attestations": "write"},
            "reserve",
            None,
        ),
        "images": (
            {
                "contents": "read",
                "actions": "read",
                "packages": "write",
                "id-token": "write",
                "attestations": "write",
            },
            "reserve",
            None,
        ),
        "assemble": (
            {
                "contents": "read",
                "actions": "read",
                "packages": "write",
                "id-token": "write",
                "attestations": "write",
            },
            ["reserve", "packages", "images"],
            None,
        ),
        "publish": (
            {"contents": "write", "actions": "read", "packages": "read"},
            ["reserve", "assemble"],
            "release",
        ),
    }
    assert set(jobs) == set(expected)
    assert " ".join(jobs["qualify"]["if"].split()) == OWNER_DISPATCH
    assert jobs["reserve"]["if"] == "inputs.publish"
    for name, job in jobs.items():
        permissions, needs, environment = expected[name]
        assert job["permissions"] == permissions
        assert job.get("needs") == needs and job.get("environment") == environment
        assert "uses" not in job and "concurrency" not in job
        if name not in ("qualify", "reserve"):
            assert job.get("if") is None
        if name == "images":
            assert job["runs-on"] == "${{ matrix.runner }}"
            assert job["strategy"]["matrix"] == {
                "include": [
                    {"arch": "amd64", "runner": "ubuntu-24.04"},
                    {"arch": "arm64", "runner": "ubuntu-24.04-arm"},
                ]
            }
        else:
            assert job["runs-on"] == "ubuntu-24.04"
        assert job["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert job["env"]["RELEASE_TAG"] == "${{ inputs.tag }}"
        steps = job["steps"]
        assert steps[0]["uses"].startswith("actions/checkout@")
        assert steps[0]["with"] == {
            "ref": "${{ github.sha }}",
            "fetch-depth": 0,
            "persist-credentials": False,
        }
        assert steps[1]["uses"].startswith("actions/setup-python@")
        if name != "qualify":
            # No upload/signing/registry login can be inserted before this
            # fresh exact-source/legal/public-tag/CI/environment preflight.
            assert steps[2]["id"] == "preparation"
            assert (
                'python tools/release_prepare.py --tag "${RELEASE_TAG}" --publish'
                in steps[2]["run"]
            )
            assert steps[2].get("if") is None
        for step in steps:
            if "uses" in step:
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
            if "run" in step:
                assert step["run"].startswith("set -euo pipefail\numask 077\n")
                assert "${{ inputs." not in step["run"]
            if "if" in step:
                assert name == "images" and step["if"] == "always()"
                assert step.get("run") == (
                    "set -euo pipefail\numask 077\nbash tools/container_install_run.sh cleanup\n"
                )
            if step.get("uses", "").startswith("actions/attest@"):
                assert name in ("packages", "images", "assemble")
                assert step["with"]["create-storage-record"] is False
                assert step["with"]["push-to-registry"] is False


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_security_workflow_pins_actions_and_uses_read_only_permissions(path):
    workflow = path.read_text()
    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    assert "persist-credentials: false" in workflow
    actions = re.findall(r"uses: ([^\s#]+)", workflow)
    assert actions and all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)
    workflow_policy(yaml.safe_load(workflow), publisher=path == PUBLISHER)


@pytest.mark.parametrize(
    "fault",
    [
        "pr_trigger",
        "implicit_publish",
        "global_write",
        "qualify_write",
        "missing_owner",
        "owner_bypass",
        "skip_stage",
        "dynamic_stage",
        "skip_candidate",
        "final_signer",
        "extra_writer",
        "unexpected_permission",
        "wrong_source_checkout",
        "persist_credentials",
        "pre_gate_upload",
        "skip_gate",
        "always_publish",
        "unpinned_action",
        "direct_input_shell",
        "untrusted_runner",
        "unconditional_upload",
        "cancel_previous",
    ],
)
def test_publisher_exception_refuses_authorization_graph_and_privilege_regressions(fault):
    value = deepcopy(yaml.safe_load(PUBLISHER.read_text()))
    jobs = value["jobs"]
    if fault == "pr_trigger":
        value["on"]["pull_request"] = None
    elif fault == "implicit_publish":
        value["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] = True
    elif fault == "global_write":
        value["permissions"]["contents"] = "write"
    elif fault == "qualify_write":
        jobs["qualify"]["permissions"]["packages"] = "write"
    elif fault == "missing_owner":
        jobs["qualify"].pop("if")
    elif fault == "owner_bypass":
        jobs["qualify"]["if"] += " || true"
    elif fault == "skip_stage":
        jobs["images"]["needs"] = "qualify"
    elif fault == "dynamic_stage":
        jobs["reserve"]["environment"] = "${{ inputs.tag }}"
    elif fault == "skip_candidate":
        jobs["publish"]["needs"] = ["reserve", "images"]
    elif fault == "final_signer":
        jobs["publish"]["permissions"]["id-token"] = "write"
    elif fault == "extra_writer":
        jobs["other"] = deepcopy(jobs["publish"])
    elif fault == "unexpected_permission":
        jobs["images"]["permissions"]["security-events"] = "write"
    elif fault == "wrong_source_checkout":
        jobs["images"]["steps"][0]["with"]["ref"] = "${{ inputs.tag }}"
    elif fault == "persist_credentials":
        jobs["images"]["steps"][0]["with"]["persist-credentials"] = True
    elif fault == "pre_gate_upload":
        jobs["images"]["steps"].insert(2, deepcopy(jobs["images"]["steps"][-1]))
    elif fault == "skip_gate":
        jobs["images"]["steps"][2]["if"] = "false"
    elif fault == "always_publish":
        jobs["publish"]["if"] = "always()"
    elif fault == "unpinned_action":
        jobs["images"]["steps"][0]["uses"] = "actions/checkout@main"
    elif fault == "direct_input_shell":
        jobs["images"]["steps"][2]["run"] += 'echo "${{ inputs.tag }}"\n'
    elif fault == "untrusted_runner":
        jobs["images"]["strategy"]["matrix"]["include"][0]["runner"] = "self-hosted"
    elif fault == "unconditional_upload":
        jobs["images"]["steps"][-1]["if"] = "always()"
    else:
        value["concurrency"]["cancel-in-progress"] = True
    with pytest.raises((AssertionError, KeyError)):
        workflow_policy(value, publisher=True)


def test_ordinary_pull_request_ci_never_gets_a_publisher_permission_exception():
    value = yaml.safe_load(WORKFLOW.read_text())
    value["jobs"]["test-and-build"]["permissions"] = {"attestations": "write"}
    with pytest.raises(AssertionError):
        workflow_policy(value)


def test_security_workflow_keeps_required_release_gates():
    workflow = WORKFLOW.read_text()
    for required in (
        "python -m pytest -q",
        "python -m ruff check .",
        "python -m build",
        "pip-audit==2.10.1",
        "cyclonedx-bom==7.4.0",
        "python -m pip_audit",
        "python -m venv .runtime-sbom",
        "cyclonedx_py environment .runtime-sbom",
        "--no-deps -r requirements-runtime.lock",
        ".runtime-sbom/bin/python -m pip check",
        "sbom.cdx.json",
        "fetch-depth: 0",
        "GITLEAKS_VERSION: 8.30.1",
        "GITLEAKS_ARCHIVE_SHA256:",
        "sha256sum --check --strict",
        'gitleaks" git --redact --no-banner --exit-code 1 .',
    ):
        assert required in workflow
    assert "GITHUB_TOKEN" not in workflow
    assert "gitleaks/gitleaks-action" not in workflow

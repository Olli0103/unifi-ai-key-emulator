"""Prepare exact-tag release metadata; never uploads, signs or grants approval.

Maintained GitHub/Sigstore actions perform cryptography and publication. This
glue makes the existing non-audit source/legal gate, explicit destinations and
live protected-environment policy prerequisites, not inferred permissions.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

REPOSITORY = "Olli0103/unifi-ai-key-emulator"
OWNER = "Olli0103"
OWNER_ID = 51629451
PUBLIC_REPOSITORY = "https://github.com/" + REPOSITORY + ".git"
WORKFLOW = REPOSITORY + "/.github/workflows/publish-release.yml@refs/heads/main"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:rc([1-9][0-9]*))?\Z")
SHA = re.compile("[0-9a-f]{40}\\Z")
HASH = re.compile("[0-9a-f]{64}\\Z")
IMAGE = re.compile(r"ghcr\.io/olli0103/[a-z0-9][a-z0-9._-]{0,100}\Z")
SCHEMA = "aikey-release-preparation/1"
CI_WORKFLOWS = {
    "security.yml": {
        "secret-scan",
        "reproducible-release",
        "test-and-build",
        "browser-ui",
        "dependency-and-sbom",
    },
    "container-install.yml": {"container-install (amd64)", "container-install (arm64)"},
}


class PreparationError(ValueError):
    """A fixed, content-free refusal; no provider messages or credentials."""


def require(condition, code):
    if not condition:
        raise PreparationError(code)


def checked_text(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def read(path, limit=65536, *, allow_empty=False):
    import stat

    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(handle, "rb") as source:
        info = os.fstat(source.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (0 if allow_empty else 1) <= info.st_size <= limit,
            "release_input_not_bounded_regular",
        )
        raw = source.read(limit + 1)
    require(len(raw) <= limit, "release_input_too_large")
    return raw


def load(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "release_json_ambiguous")
            value[key] = item
        return value

    def reject(_):
        raise PreparationError("release_json_nonfinite")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    require(isinstance(value, dict), "release_object_required")
    return value


def command(argv, *, cwd, timeout=20):
    """Only fixed git/gh metadata commands, bounded file-backed output."""
    require(argv and argv[0] in {"git", "gh"}, "release_metadata_command_refused")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
    with tempfile.TemporaryFile(dir=cwd) as output:
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PreparationError("release_metadata_unavailable") from None
        require(result.returncode == 0, "release_metadata_unavailable")
        require(output.tell() <= 128 * 1024, "release_metadata_too_large")
        output.seek(0)
        return output.read().decode("utf-8")


def publication(record, tag):
    """A record is an attestation to review, not proof of its human truth."""
    value = record.get("publication")
    fields = {
        "status",
        "reviewer",
        "evidence_ref",
        "tag",
        "package_destination",
        "images",
        "signing_method",
        "github_environment",
        "staging_environment",
    }
    require(isinstance(value, dict) and set(value) == fields, "release_publication_record_required")
    require(
        value["status"] == "verified"
        and value["reviewer"] == OWNER
        and checked_text(value["evidence_ref"]),
        "release_maintainer_decision_required",
    )
    require(value["tag"] == tag, "release_approval_tag_mismatch")
    require(
        value["package_destination"] == "github-release"
        and value["signing_method"] == "github_oidc_sigstore"
        and value["github_environment"] == "release",
        "release_destination_or_signing_unsupported",
    )
    require(value["staging_environment"] == "release-stage", "release_staging_environment_required")
    images = value["images"]
    require(
        isinstance(images, dict)
        and set(images) == {"aikey", "aiport"}
        and all(isinstance(image, str) and IMAGE.fullmatch(image) for image in images.values())
        and len(set(images.values())) == 2,
        "release_image_destinations_refused",
    )
    return value


def protected_environment(value, name="release"):
    """A name alone must not silently create an unprotected environment."""
    require(
        isinstance(value, dict)
        and value.get("name") == name
        and type(value.get("id")) is int
        and value["id"] > 0
        and isinstance(value.get("protection_rules"), list),
        "release_environment_unavailable",
    )
    rules = [
        row
        for row in value["protection_rules"]
        if isinstance(row, dict) and row.get("type") == "required_reviewers"
    ]
    require(
        len(rules) == 1 and isinstance(rules[0].get("reviewers"), list),
        "release_environment_human_review_required",
    )
    reviewers = rules[0]["reviewers"]
    require(
        reviewers and all(isinstance(row, dict) for row in reviewers),
        "release_environment_human_review_required",
    )
    # Do not accept a bot, same-name account, unknown team or a different
    # alternative approver as permission for the maintainer's first release.
    require(
        all(
            row.get("type") == "User"
            and isinstance(row.get("reviewer"), dict)
            and row["reviewer"].get("login") == OWNER
            and type(row["reviewer"].get("id")) is int
            and row["reviewer"]["id"] == OWNER_ID
            and row["reviewer"].get("type", "User") == "User"
            for row in reviewers
        ),
        "release_environment_maintainer_reviewer_required",
    )
    return value["id"]


def workflow_context(env, revision):
    require(
        env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_REPOSITORY") == REPOSITORY
        and env.get("GITHUB_REF") == "refs/heads/main"
        and env.get("GITHUB_WORKFLOW_REF") == WORKFLOW
        and env.get("GITHUB_ACTOR") == OWNER
        and env.get("GITHUB_TRIGGERING_ACTOR") == OWNER,
        "release_requires_maintainer_main_dispatch",
    )
    require(env.get("GITHUB_SHA") == revision, "release_workflow_source_provenance_mismatch")
    require(
        env.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and env.get("RUNNER_OS") == "Linux"
        and env.get("RUNNER_ARCH") in {"X64", "ARM64"},
        "release_requires_hosted_native_linux",
    )
    run = env.get("GITHUB_RUN_ID", "")
    require(
        isinstance(run, str)
        and re.fullmatch("[1-9][0-9]{0,19}", run)
        and env.get("GITHUB_RUN_ATTEMPT") == "1",
        "release_rerun_requires_reconciliation",
    )
    return run


def public_tag(root, tag, revision, run=command):
    text = run(
        ["git", "ls-remote", PUBLIC_REPOSITORY, "refs/tags/" + tag, "refs/tags/" + tag + "^{}"],
        cwd=root,
    )
    require(len(text) <= 4096, "release_public_tag_unavailable")
    rows = {}
    for line in text.splitlines():
        parts = line.split("\t")
        require(
            len(parts) == 2
            and SHA.fullmatch(parts[0])
            and parts[1] in {"refs/tags/" + tag, "refs/tags/" + tag + "^{}"}
            and parts[1] not in rows,
            "release_public_tag_unavailable",
        )
        rows[parts[1]] = parts[0]
    require(
        "refs/tags/" + tag in rows
        and rows.get("refs/tags/" + tag + "^{}", rows["refs/tags/" + tag]) == revision,
        "release_public_tag_mismatch",
    )
    return rows["refs/tags/" + tag]


def continuous_integration(root, revision, run=command):
    """Fresh complete actual job sets, not bot status contexts or old heads."""
    verified = []
    for workflow, names in CI_WORKFLOWS.items():
        target = (
            f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs"
            f"?head_sha={revision}&event=push&per_page=100"
        )
        listing = load(run(["gh", "api", target], cwd=root))
        rows = listing.get("workflow_runs")
        require(
            isinstance(rows, list)
            and 0 < len(rows) <= 100
            and type(listing.get("total_count")) is int
            and listing["total_count"] == len(rows),
            "release_ci_listing_incomplete",
        )
        require(
            all(
                isinstance(row, dict) and type(row.get("id")) is int and row["id"] > 0
                for row in rows
            ),
            "release_ci_run_refused",
        )
        latest = max(rows, key=lambda row: row["id"])
        require(
            latest.get("head_sha") == revision
            and latest.get("head_branch") == "main"
            and latest.get("event") == "push"
            and latest.get("path") == ".github/workflows/" + workflow
            and latest.get("status") == "completed"
            and latest.get("conclusion") == "success",
            "release_ci_not_successful_current_main",
        )
        jobs = load(
            run(
                ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{latest['id']}/jobs?per_page=100"],
                cwd=root,
            )
        )
        values = jobs.get("jobs")
        require(
            isinstance(values, list)
            and len(values) == len(names)
            and type(jobs.get("total_count")) is int
            and jobs["total_count"] == len(values),
            "release_ci_jobs_incomplete",
        )
        require(
            all(
                isinstance(job, dict)
                and type(job.get("id")) is int
                and job["id"] > 0
                and job.get("status") == "completed"
                and job.get("conclusion") == "success"
                for job in values
            )
            and {job.get("name") for job in values} == names
            and len({job["id"] for job in values}) == len(values),
            "release_ci_jobs_not_successful",
        )
        verified.append(
            {
                "workflow": workflow,
                "run_id": latest["id"],
                "job_ids": sorted(job["id"] for job in values),
            }
        )
    identities = [identifier for row in verified for identifier in row["job_ids"]]
    require(len(identities) == len(set(identities)) == 7, "release_ci_jobs_not_distinct")
    return verified


def prepare(root, tag, *, publish=False, env=None, run=command):
    root = Path(root).absolute()
    require(
        root.resolve() == root and isinstance(tag, str) and TAG.fullmatch(tag),
        "release_tag_or_root_refused",
    )
    require(
        run(["git", "rev-parse", "--show-toplevel"], cwd=root).strip() == str(root),
        "release_repository_root_mismatch",
    )
    require(
        not run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root).strip(),
        "release_source_not_clean",
    )
    revision = run(["git", "rev-parse", "HEAD"], cwd=root).strip()
    require(SHA.fullmatch(revision), "release_revision_refused")
    project = tomllib.loads(read(root / "pyproject.toml").decode())["project"]
    require(
        project.get("name") == "local-aikey" and project.get("version") == tag[1:],
        "release_tag_package_version_mismatch",
    )
    require(
        run(["git", "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}"], cwd=root).strip()
        == revision,
        "release_local_tag_mismatch",
    )
    # Use the trusted launcher's paired policy, never execute a policy module
    # supplied by a target checkout before that source is authenticated.
    path = Path(__file__).resolve().with_name("community_release_check.py")
    spec = importlib.util.spec_from_file_location("reviewed_community_gate", path)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    paths = gate.tracked_paths(root)
    snapshot = gate.source_snapshot(root, paths)
    record = load(read(root / "docs/legal/release-evidence.json"))
    import contextlib
    import io

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        status = gate.main(["--root", str(root)])  # Deliberately no audit-only bypass.
    report = load(captured.getvalue())
    require(
        status == 0
        and report.get("issues") == []
        and report.get("release_approved") is True
        and report.get("source_snapshot_sha256") == snapshot,
        "release_non_audit_evidence_gate_blocked",
    )
    decision = publication(record, tag)
    require(
        not run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root).strip(),
        "release_source_changed_during_preparation",
    )
    tag_object = public_tag(root, tag, revision, run)
    environment_id = None
    staging_environment_id = None
    run_id = None
    ci = None
    if publish:
        run_id = workflow_context(os.environ if env is None else env, revision)
        ci = continuous_integration(root, revision, run)
        environment = load(
            run(["gh", "api", "repos/" + REPOSITORY + "/environments/release"], cwd=root)
        )
        environment_id = protected_environment(environment)
        staging = load(
            run(["gh", "api", "repos/" + REPOSITORY + "/environments/release-stage"], cwd=root)
        )
        staging_environment_id = protected_environment(staging, "release-stage")
    timestamp = run(["git", "show", "-s", "--format=%ct", revision], cwd=root).strip()
    require(
        timestamp.isdigit() and 0 < int(timestamp) < 1 << 40, "release_source_timestamp_refused"
    )
    require(
        run(["git", "rev-parse", "HEAD"], cwd=root).strip() == revision
        and not run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root).strip()
        and gate.source_snapshot(root, paths) == snapshot
        and load(read(root / "docs/legal/release-evidence.json")) == record,
        "release_source_changed_during_preparation",
    )
    return {
        "schema": SCHEMA,
        "source_revision": revision,
        "source_snapshot_sha256": snapshot,
        "tag": tag,
        "tag_object": tag_object,
        "version": project["version"],
        "prerelease": TAG.fullmatch(tag).group(4) is not None,
        "source_date_epoch": int(timestamp),
        "package_destination": decision["package_destination"],
        "images": decision["images"],
        "signing_method": decision["signing_method"],
        "environment": "release",
        "environment_id": environment_id,
        "staging_environment_id": staging_environment_id,
        "workflow_run_id": run_id,
        "continuous_integration": ci,
        "publication_requested": publish,
        "uploaded": False,
        "signed": False,
        "human_evidence_truth": "not_established_by_automation",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="also require live maintainer workflow/environment policy",
    )
    args = parser.parse_args(argv)
    try:
        value = prepare(args.root, args.tag, publish=args.publish)
        print(json.dumps(value, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        code = (
            error.args[0]
            if isinstance(error, PreparationError) and error.args
            else "release_preparation_unavailable"
        )
        print(json.dumps({"schema": SCHEMA, "error": code, "uploaded": False, "signed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

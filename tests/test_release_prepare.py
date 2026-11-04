"""Release metadata gates only. Fictional records never approve this repository."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_prepare", Path(__file__).resolve().parents[1] / "tools/release_prepare.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def decision():
    return {
        "publication": {
            "status": "verified",
            "reviewer": gate.OWNER,
            "evidence_ref": "fictional-test-record-not-a-real-approval",
            "tag": "v0.1.0rc1",
            "package_destination": "github-release",
            "images": {
                "aikey": "ghcr.io/olli0103/fictional-key",
                "aiport": "ghcr.io/olli0103/fictional-port",
            },
            "signing_method": "github_oidc_sigstore",
            "github_environment": "release",
            "staging_environment": "release-stage",
        }
    }


def environment():
    return {
        "id": 12,
        "name": "release",
        "protection_rules": [
            {
                "type": "required_reviewers",
                "reviewers": [
                    {
                        "type": "User",
                        "reviewer": {"login": gate.OWNER, "id": gate.OWNER_ID, "type": "User"},
                    }
                ],
            }
        ],
    }


def test_fictional_maintainer_decision_is_not_itself_an_upload_or_signature():
    assert gate.publication(decision(), "v0.1.0rc1")["package_destination"] == "github-release"
    assert gate.protected_environment(environment()) == 12


@pytest.mark.parametrize(
    "fault",
    [
        "absent",
        "pending",
        "reviewer",
        "tag",
        "images",
        "namespace",
        "same_images",
        "signing",
        "environment",
        "extra",
    ],
)
def test_missing_or_foreign_decision_never_becomes_release_authority(fault):
    value = decision()
    record = value["publication"]
    if fault == "absent":
        value = {}
    elif fault == "pending":
        record["status"] = "needs_evidence"
    elif fault == "reviewer":
        record["reviewer"] = "fictional-other-reviewer"
    elif fault == "tag":
        record["tag"] = "v0.1.0rc2"
    elif fault == "images":
        record["images"]["unexpected"] = "ghcr.io/olli0103/other"
    elif fault == "namespace":
        record["images"]["aikey"] = "ghcr.io/other/fictional-key"
    elif fault == "same_images":
        record["images"]["aikey"] = record["images"]["aiport"]
    elif fault == "signing":
        record["signing_method"] = "unreviewed-key-path"
    elif fault == "environment":
        record["github_environment"] = "silently-created-env"
    else:
        record["extra"] = True
    with pytest.raises(gate.PreparationError):
        gate.publication(value, "v0.1.0rc1")


@pytest.mark.parametrize(
    "fault",
    [
        "no_rules",
        "timer_only",
        "different_id",
        "different_login",
        "bot",
        "team",
        "alternative_reviewer",
        "bool_id",
    ],
)
def test_environment_name_or_green_bot_cannot_replace_maintainer_review(fault):
    value = environment()
    rule = value["protection_rules"][0]
    reviewer = rule["reviewers"][0]
    if fault == "no_rules":
        value["protection_rules"] = []
    elif fault == "timer_only":
        rule["type"] = "wait_timer"
    elif fault == "different_id":
        reviewer["reviewer"]["id"] = 1
    elif fault == "different_login":
        reviewer["reviewer"]["login"] = "someone-else"
    elif fault == "bot":
        reviewer["reviewer"]["type"] = "Bot"
    elif fault == "team":
        reviewer["type"] = "Team"
    elif fault == "alternative_reviewer":
        rule["reviewers"].append({"type": "User", "reviewer": {"login": "other", "id": 99}})
    else:
        value["id"] = True
    with pytest.raises(gate.PreparationError):
        gate.protected_environment(value)


def test_exact_public_tag_not_a_lookalike_or_unrelated_peeled_commit(tmp_path):
    sha = "a" * 40

    def run(argv, **kwargs):
        assert argv == [
            "git",
            "ls-remote",
            gate.PUBLIC_REPOSITORY,
            "refs/tags/v0.1.0rc1",
            "refs/tags/v0.1.0rc1^{}",
        ]
        return "b" * 40 + "\trefs/tags/v0.1.0rc1\n" + sha + "\trefs/tags/v0.1.0rc1^{}\n"

    assert gate.public_tag(tmp_path, "v0.1.0rc1", sha, run) == "b" * 40
    with pytest.raises(gate.PreparationError):
        gate.public_tag(tmp_path, "v0.1.0rc1", "c" * 40, run)


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', "[]"])
def test_ambiguous_metadata_refuses_without_echoing_payload(raw):
    with pytest.raises((ValueError, gate.PreparationError)):
        gate.load(raw)


def test_ci_needs_complete_fresh_workflow_jobs_not_a_success_status_context(tmp_path):
    revision = "a" * 40
    for fault in ("none", "wrong_head", "extra_page", "failed_job", "bot_instead", "duplicate_job"):

        def run(argv, **kwargs):
            target = argv[-1]
            if "/workflows/" in target:
                path = next(name for name in gate.CI_WORKFLOWS if name in target)
                row = {
                    "id": 1 if path == "security.yml" else 2,
                    "head_sha": revision if fault != "wrong_head" else "b" * 40,
                    "head_branch": "main",
                    "event": "push",
                    "path": ".github/workflows/" + path,
                    "status": "completed",
                    "conclusion": "success",
                }
                return json.dumps(
                    {"total_count": 2 if fault == "extra_page" else 1, "workflow_runs": [row]}
                )
            names = gate.CI_WORKFLOWS[
                "security.yml" if "/runs/1/" in target else "container-install.yml"
            ]
            jobs = [
                {"id": number, "name": name, "status": "completed", "conclusion": "success"}
                for number, name in enumerate(
                    sorted(names), start=10 if "/runs/1/" in target else 20
                )
            ]
            if fault == "failed_job":
                jobs[0]["conclusion"] = "failure"
            if fault == "bot_instead":
                jobs[0]["name"] = "CodeRabbit"
            if fault == "duplicate_job":
                jobs[1]["id"] = jobs[0]["id"]
            return json.dumps({"total_count": len(jobs), "jobs": jobs})

        if fault == "none":
            assert (
                sum(
                    len(row["job_ids"])
                    for row in gate.continuous_integration(tmp_path, revision, run)
                )
                == 7
            )
        else:
            with pytest.raises(gate.PreparationError):
                gate.continuous_integration(tmp_path, revision, run)


@pytest.fixture
def tagged_source(tmp_path):
    """Real disposable Git/tag bytes; all approvals are explicitly fictional."""
    import os
    import subprocess
    import hashlib

    root = tmp_path / "source"
    root.mkdir()
    (root / "tools").mkdir()
    (root / "docs/legal").mkdir(parents=True)
    project = Path(__file__).resolve().parents[1]
    (root / "tools/community_release_check.py").write_bytes(
        (project / "tools/community_release_check.py").read_bytes()
    )
    (root / "LICENSE").write_bytes((project / "LICENSE").read_bytes())
    (root / "pyproject.toml").write_text('[project]\nname="local-aikey"\nversion="0.1.0rc1"\n')
    (root / "README.md").write_text("FICTIONAL RELEASE FIXTURE ONLY. No real approval.\n")
    spec = importlib.util.spec_from_file_location(
        "fictional_community", root / "tools/community_release_check.py"
    )
    community = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(community)
    record = {
        "schema": "community-release-evidence/1",
        "reviewed_snapshot_sha256": None,
        "checks": {
            name: {
                "status": "verified",
                "reviewer": "FICTIONAL-test-reviewer",
                "evidence_ref": "FICTIONAL-test-only-not-real-permission",
            }
            for name in community.REQUIRED
        },
        **decision(),
    }
    evidence = root / "docs/legal/release-evidence.json"
    evidence.write_text(json.dumps(record))
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="Fictional fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Fictional fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
    )

    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, env=env, stderr=subprocess.DEVNULL, text=True
        ).strip()

    git("init", "-b", "main")
    git("add", ".")
    record["reviewed_snapshot_sha256"] = community.source_snapshot(
        root, community.tracked_paths(root)
    )
    evidence.write_text(json.dumps(record))
    git("add", ".")
    git("commit", "-m", "Fictional release gate fixture, not real approval")
    git("tag", "v0.1.0rc1")
    revision = git("rev-parse", "HEAD")
    calls = []

    def run(argv, **kwargs):
        if argv[:2] == ["git", "ls-remote"]:
            calls.append(argv)
            return revision + "\trefs/tags/v0.1.0rc1\n"
        if argv[:2] == ["gh", "api"]:
            calls.append(argv)
            if "/environments/" in argv[-1]:
                value = environment()
                if argv[-1].endswith("/release-stage"):
                    value.update(name="release-stage", id=13)
                return json.dumps(value)
            if "/workflows/" in argv[-1]:
                path = next(name for name in gate.CI_WORKFLOWS if name in argv[-1])
                return json.dumps(
                    {
                        "total_count": 1,
                        "workflow_runs": [
                            {
                                "id": 1 if path == "security.yml" else 2,
                                "head_sha": revision,
                                "head_branch": "main",
                                "event": "push",
                                "path": ".github/workflows/" + path,
                                "status": "completed",
                                "conclusion": "success",
                            }
                        ],
                    }
                )
            security = "/runs/1/" in argv[-1]
            names = gate.CI_WORKFLOWS["security.yml" if security else "container-install.yml"]
            jobs = [
                {"id": number, "name": name, "status": "completed", "conclusion": "success"}
                for number, name in enumerate(sorted(names), start=10 if security else 20)
            ]
            return json.dumps({"total_count": len(jobs), "jobs": jobs})
        return gate.command(argv, **kwargs)

    context = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": gate.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": gate.WORKFLOW,
        "GITHUB_ACTOR": gate.OWNER,
        "GITHUB_TRIGGERING_ACTOR": gate.OWNER,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": revision,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "RUNNER_ARCH": "X64",
    }
    before = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }
    return root, run, context, calls, before


def test_real_clean_tagged_fixture_uses_non_audit_gate_and_preserves_all_bytes(tagged_source):
    import hashlib

    root, run, context, calls, before = tagged_source
    result = gate.prepare(root, "v0.1.0rc1", publish=True, env=context, run=run)
    assert result["source_revision"] == context["GITHUB_SHA"] and result["prerelease"] is True
    assert result["environment_id"] == 12 and result["publication_requested"] is True
    assert result["uploaded"] is False and result["signed"] is False
    assert sum(len(row["job_ids"]) for row in result["continuous_integration"]) == 7
    assert len(calls) == 7  # Public tag, four CI and two protected-environment reads, never writes.
    assert all(
        hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        for name, digest in before.items()
    )


@pytest.mark.parametrize(
    "fault",
    ["unreviewed", "missing_fingerprint", "empty_license", "dirty_source", "different_version"],
)
def test_real_source_refusals_precede_any_remote_metadata_or_publisher(tagged_source, fault):
    import subprocess
    import os

    root, run, context, calls, _ = tagged_source
    if fault == "dirty_source":
        (root / "README.md").write_text("FICTIONAL unreviewed input")
    elif fault == "different_version":
        pass
    else:
        path = root / "docs/legal/release-evidence.json"
        value = json.loads(path.read_text())
        if fault == "unreviewed":
            value["checks"]["legal_review"]["status"] = "needs_evidence"
        elif fault == "missing_fingerprint":
            value["reviewed_snapshot_sha256"] = None
        else:
            (root / "LICENSE").write_text("")
        path.write_text(json.dumps(value))
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="Fictional fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fictional fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        subprocess.run(["git", "add", "."], cwd=root, env=env, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Fictional refusal"],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "tag", "-f", "v0.1.0rc1"], cwd=root, env=env, check=True, capture_output=True
        )
    with pytest.raises(gate.PreparationError):
        gate.prepare(
            root,
            "v0.1.0rc2" if fault == "different_version" else "v0.1.0rc1",
            publish=True,
            env=context,
            run=run,
        )
    assert calls == []


@pytest.mark.parametrize(
    "field",
    [
        "GITHUB_EVENT_NAME",
        "GITHUB_REPOSITORY",
        "GITHUB_REF",
        "GITHUB_WORKFLOW_REF",
        "GITHUB_ACTOR",
        "GITHUB_TRIGGERING_ACTOR",
        "GITHUB_SHA",
        "GITHUB_RUN_ATTEMPT",
        "RUNNER_ENVIRONMENT",
        "RUNNER_OS",
        "RUNNER_ARCH",
    ],
)
def test_current_workflow_identity_and_actual_built_source_must_agree(tagged_source, field):
    root, run, context, calls, _ = tagged_source
    context[field] = "fictional-other"
    with pytest.raises(gate.PreparationError):
        gate.prepare(root, "v0.1.0rc1", publish=True, env=context, run=run)
    assert len(calls) == 1 and calls[0][:2] == ["git", "ls-remote"]


def test_cli_rejects_shell_shape_before_git_and_never_echoes_the_input(capsys, tmp_path):
    assert gate.main(["--root", str(tmp_path), "--tag", "v1.0.0;FICTIONAL_CANARY"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert (
        result["error"] == "release_tag_or_root_refused"
        and result["uploaded"] is result["signed"] is False
    )
    assert "FICTIONAL_CANARY" not in json.dumps(result)


def test_a_metadata_read_cannot_hide_a_changed_local_source(tagged_source):
    root, run, context, _, _ = tagged_source

    def drifting(argv, **kwargs):
        result = run(argv, **kwargs)
        if argv[:2] == ["gh", "api"] and argv[-1].endswith("/environments/release"):
            (root / "README.md").write_text("FICTIONAL source drift during remote reads")
        return result

    with pytest.raises(gate.PreparationError, match="source_changed"):
        gate.prepare(root, "v0.1.0rc1", publish=True, env=context, run=drifting)


def test_empty_source_bytes_are_explicit_not_empty_approval_records(tmp_path):
    path = tmp_path / "empty.py"
    path.write_bytes(b"")
    with pytest.raises(gate.PreparationError):
        gate.read(path)
    assert gate.read(path, allow_empty=True) == b""

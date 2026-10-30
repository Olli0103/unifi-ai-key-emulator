"""A frozen deployment's project must win over ambient Compose selection."""

from copy import deepcopy
import json

import pytest

from aikey import aiport_nas_compose as compose
from aikey import aiport_nas_reconcile as reconciler
from test_aiport_nas_reconcile import FakeDocker, inputs


INVALID = ["", "UPPER", "../elsewhere", "a/b", "white space", "bad\nline",
           "--project-name", "a.b", "a" * 64, None, True, 7, [], {}]


@pytest.mark.parametrize("name", ["local-aiport", "local-aiport-nas", "a", "p_12",
                                  "a" * 63, "up", "config"])
def test_generator_keeps_explicit_bounded_project_name(tmp_path, name):
    plan, states, options, _, report = inputs(tmp_path)
    manifest = compose.build_nas_compose(plan, states, project_name=name, **options)
    assert manifest["name"] == name
    assert reconciler.verify_inputs(plan, manifest, report, states,
                                   {**options, "project_name": name}) == ["aiport_slot_2"]
    changed = deepcopy(manifest)
    changed["name"] = "unreviewed-project"
    with pytest.raises(reconciler.ReconcileError, match="manifest"):
        reconciler.verify_inputs(plan, changed, report, states,
                                {**options, "project_name": name})


@pytest.mark.parametrize("name", INVALID)
def test_generator_refuses_invalid_project_before_provisioning(tmp_path, name, monkeypatch):
    plan, states, options, _, _ = inputs(tmp_path)
    calls = []
    monkeypatch.setattr(compose, "provision_slot", lambda *a, **k: calls.append(True))
    with pytest.raises(compose.NasComposeError, match="project"):
        compose.build_nas_compose(plan, states, project_name=name, **options)
    assert not calls


@pytest.mark.parametrize("name", INVALID)
def test_reconciler_refuses_invalid_project_before_any_docker_command(tmp_path, name):
    _, states, _, manifest, _ = inputs(tmp_path)
    manifest["name"] = name
    fake = FakeDocker(manifest)
    with pytest.raises(reconciler.ReconcileError, match="project"):
        reconciler.reconcile(tmp_path / "compose.json", manifest, states,
                             run=fake)
    assert not fake.calls


class NamespacedDocker(FakeDocker):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.original_calls = []
        self.project_selections = []

    def __call__(self, argv, timeout):
        self.original_calls.append(argv)
        if argv[:2] == ["docker", "compose"]:
            assert argv[2:4] == ["--project-name", self.manifest["name"]]
            self.project_selections.append(argv[3])
            # The old fixture's command matcher must not interpret a project's
            # legitimate name "up" or "config" as the requested operation.
            argv = argv[:2] + argv[4:]
        return super().__call__(argv, timeout)


@pytest.mark.parametrize("name", ["local-aiport", "local-aiport-nas", "up", "config"])
@pytest.mark.parametrize("mode", ["dry", "apply", "rollback"])
def test_every_compose_boundary_uses_reviewed_project_not_environment(tmp_path, monkeypatch,
                                                                    name, mode):
    plan, states, options, _, _ = inputs(tmp_path)
    manifest = compose.build_nas_compose(plan, states, project_name=name, **options)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unrelated-project")
    fake = NamespacedDocker(manifest, fail_health=mode == "rollback")
    if mode == "rollback":
        with pytest.raises(reconciler.ReconcileError, match="health"):
            reconciler.reconcile(tmp_path / "reviewed.json", manifest, states,
                                 apply=True, run=fake, pause=lambda _: None)
        assert any("stop" in call for call in fake.calls)
    else:
        result = reconciler.reconcile(tmp_path / "reviewed.json", manifest, states,
                                      apply=mode == "apply", run=fake, pause=lambda _: None)
        assert result["mode"] == ("dry_run" if mode == "dry" else "applied")
    assert fake.project_selections and set(fake.project_selections) == {name}
    assert all("unrelated-project" not in call for call in fake.original_calls)
    if mode != "dry":
        assert any("up" in call for call in fake.calls)
        assert any("exec" in call for call in fake.calls)


def arguments(plan, states, options, tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    path.chmod(0o600)
    args = ["--plan", str(path)]
    for slot, state in states.items():
        args += ["--slot-state", f"{slot}={state}"]
    for option in ("controller_ip", "controller_pin", "nas_ip", "subnet", "gateway",
                   "parent", "image", "uid", "gid"):
        args += ["--" + option.replace("_", "-"), str(options[option])]
    return args


def test_compose_cli_serializes_the_explicit_project(tmp_path, capsys):
    plan, states, options, _, _ = inputs(tmp_path)
    output = tmp_path / "manifest.json"
    assert compose.main(arguments(plan, states, options, tmp_path)
                        + ["--project-name", "native-case", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["name"] == "native-case"
    capsys.readouterr()


def test_reconciler_cli_verifies_the_selected_project_without_native_requests(tmp_path,
                                                                            monkeypatch,
                                                                            capsys):
    plan, states, options, _, report = inputs(tmp_path)
    manifest = compose.build_nas_compose(plan, states, project_name="native-case", **options)
    output = tmp_path / "manifest.json"
    output.write_text(json.dumps(manifest))
    output.chmod(0o600)

    async def inventory(*args, **kwargs):
        return report

    calls = []
    monkeypatch.setattr(reconciler, "fetch_inventory", inventory)
    monkeypatch.setattr(reconciler, "reconcile", lambda path, value, selected, **kwargs:
                        calls.append((value["name"], kwargs["apply"])) or {"mode": "dry_run"})
    args = arguments(plan, states, options, tmp_path) + [
        "--compose", str(output), "--project-name", "native-case",
        "--api-key-file", str(tmp_path / "fictional-key-not-read"),
        "--web-trust-file", str(tmp_path / "fictional-trust-not-read"),
        "--web-cert-file", str(tmp_path / "fictional-cert-not-read")]
    assert reconciler.main(args) == 0
    assert calls == [("native-case", False)]
    capsys.readouterr()

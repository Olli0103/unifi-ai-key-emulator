"""Optional deployment details are explicit inputs, never unchecked exceptions."""

from copy import deepcopy
import json

import pytest

from aikey import aiport_nas_compose as compose
from aikey import aiport_nas_reconcile as reconciler
from test_aiport_nas_reconcile import FakeDocker, inputs
from test_aiport_nas_compose import fixture
from test_compose_project_fencing import arguments


def layout(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    return {
        "schema": "aikey-nas-runtime-layout/1",
        "slot_images": {"2": "local-aiport:reviewed-other"},
        "models_dir": str(models),
        "health_network": {
            "name": "fictional_health",
            "subnet": "172.27.1.0/24",
            "gateway": "172.27.1.1",
            "addresses": {"2": "172.27.1.22"},
        },
    }


def test_layout_is_frozen_and_default_stays_minimal(tmp_path):
    plan, states, options, default, report = inputs(tmp_path)
    value = layout(tmp_path)
    manifest = compose.build_nas_compose(plan, states, runtime_layout=value, **options)
    service = manifest["services"]["aiport_slot_2"]
    assert service["image"] == value["slot_images"]["2"]
    assert service["volumes"][1] == {
        "type": "bind",
        "source": value["models_dir"],
        "target": "/models",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    assert service["networks"]["aiport_health"] == {"ipv4_address": "172.27.1.22"}
    assert service["networks"]["aiport_lan"]["gw_priority"] == 1
    assert manifest["networks"]["aiport_health"] == {"external": True, "name": "fictional_health"}
    assert reconciler.verify_inputs(
        plan, manifest, report, states, {**options, "runtime_layout": value}
    ) == ["aiport_slot_2"]
    changed = deepcopy(manifest)
    changed["services"]["aiport_slot_2"]["volumes"][1]["read_only"] = False
    with pytest.raises(reconciler.ReconcileError, match="manifest"):
        reconciler.verify_inputs(
            plan, changed, report, states, {**options, "runtime_layout": value}
        )
    assert len(default["services"]["aiport_slot_2"]["volumes"]) == 1
    assert set(default["networks"]) == {"aiport_lan"}
    assert default == compose.build_nas_compose(plan, states, **options)


@pytest.mark.parametrize(
    "bad",
    [
        "schema",
        "unknown",
        "not_object",
        "images_type",
        "image_unselected",
        "image_noncanonical_slot",
        "image_invalid",
        "models_relative",
        "models_missing",
        "models_symlink",
        "health_unknown",
        "health_name",
        "health_subnet",
        "health_public",
        "health_overlap",
        "health_unselected",
        "health_missing_address",
        "health_bad_address",
        "health_network_address",
        "health_broadcast_address",
        "health_gateway_address",
        "health_missing_gateway",
        "health_bad_gateway",
    ],
)
def test_layout_rejected_before_provision_or_host_creation(tmp_path, monkeypatch, bad):
    plan, states, options, _, _ = inputs(tmp_path)
    value = layout(tmp_path)
    if bad == "schema":
        value["schema"] = "other/1"
    elif bad == "unknown":
        value["volumes"] = []
    elif bad == "not_object":
        value = []
    elif bad == "images_type":
        value["slot_images"] = []
    elif bad == "image_unselected":
        value["slot_images"]["1"] = "example:other"
    elif bad == "image_noncanonical_slot":
        value["slot_images"]["02"] = "example:other"
    elif bad == "image_invalid":
        value["slot_images"]["2"] = "image\n--privileged"
    elif bad == "models_relative":
        value["models_dir"] = "relative-models"
    elif bad == "models_missing":
        value["models_dir"] = str(tmp_path / "not-created")
    elif bad == "models_symlink":
        link = tmp_path / "linked-models"
        link.symlink_to(value["models_dir"], target_is_directory=True)
        value["models_dir"] = str(link)
    elif bad == "health_unknown":
        value["health_network"]["driver"] = "host"
    elif bad == "health_name":
        value["health_network"]["name"] = "bad name"
    elif bad == "health_subnet":
        value["health_network"]["subnet"] = "172.27.1.3/24"
    elif bad == "health_public":
        value["health_network"]["subnet"] = "8.8.8.0/24"
    elif bad == "health_overlap":
        value["health_network"]["subnet"] = options["subnet"]
    elif bad == "health_unselected":
        value["health_network"]["addresses"]["1"] = "172.27.1.23"
    elif bad == "health_missing_address":
        value["health_network"]["addresses"] = {}
    elif bad == "health_bad_address":
        value["health_network"]["addresses"]["2"] = "172.27.2.22"
    elif bad == "health_network_address":
        value["health_network"]["addresses"]["2"] = "172.27.1.0"
    elif bad == "health_broadcast_address":
        value["health_network"]["addresses"]["2"] = "172.27.1.255"
    elif bad == "health_gateway_address":
        value["health_network"]["addresses"]["2"] = "172.27.1.1"
    elif bad == "health_missing_gateway":
        del value["health_network"]["gateway"]
    elif bad == "health_bad_gateway":
        value["health_network"]["gateway"] = "172.27.2.1"
    calls = []
    monkeypatch.setattr(compose, "provision_slot", lambda *a, **k: calls.append(True))
    with pytest.raises(compose.NasComposeError, match="layout|model|health|image"):
        compose.build_nas_compose(plan, states, runtime_layout=value, **options)
    assert not calls and not (tmp_path / "not-created").exists()


class LayoutDocker(FakeDocker):
    def __init__(self, manifest, mutation=None):
        super().__init__(manifest)
        self.started = True
        self.mutation = mutation

    def __call__(self, argv, timeout):
        service = self.manifest["services"]["aiport_slot_2"]
        if argv[:3] == ["docker", "network", "inspect"]:
            self.calls.append(argv)
            contract = self.manifest["x-aikey-health-network-check"]
            value = {
                "Name": contract["name"],
                "Driver": "bridge",
                "Internal": True,
                "EnableIPv6": False,
                "IPAM": {
                    "Config": [{"Subnet": contract["subnet"], "Gateway": contract["gateway"]}]
                },
            }
            if self.mutation == "driver":
                value["Driver"] = "macvlan"
            elif self.mutation == "internal":
                value["Internal"] = False
            elif self.mutation == "ipv6":
                value["EnableIPv6"] = True
            elif self.mutation == "subnet":
                value["IPAM"]["Config"][0]["Subnet"] = "172.28.0.0/24"
            elif self.mutation == "gateway":
                value["IPAM"]["Config"][0]["Gateway"] = "172.27.1.2"
            elif self.mutation == "extra_ipam":
                value["IPAM"]["Config"].append({"Subnet": "172.29.0.0/24"})
            elif self.mutation == "name":
                value["Name"] = "other_health"
            return json.dumps(value)
        if argv[:2] == ["docker", "inspect"] and ".Mounts" in argv[3]:
            self.calls.append(argv)
            mounts = [
                {
                    "Type": "bind",
                    "Source": v["source"],
                    "Destination": v["target"],
                    "RW": not v.get("read_only", False),
                }
                for v in reversed(service["volumes"])
            ]
            if self.mutation == "models_rw":
                mounts[0]["RW"] = True
            elif self.mutation == "models_source":
                mounts[0]["Source"] = "/wrong/models"
            elif self.mutation == "models_missing":
                mounts.pop(0)
            elif self.mutation == "duplicate_mount":
                mounts.append(mounts[0])
            elif self.mutation == "extra_mount":
                mounts.append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": False})
            return json.dumps(mounts)
        if argv[:2] == ["docker", "inspect"] and ".NetworkSettings.Networks" in argv[3]:
            self.calls.append(argv)
            lan = service["networks"]["aiport_lan"]
            health = self.manifest["x-aikey-health-network-check"]
            value = {
                self.manifest["name"] + "_aiport_lan": {
                    "IPAddress": lan["ipv4_address"],
                    "MacAddress": lan["mac_address"],
                },
                health["name"]: {"IPAddress": service["networks"]["aiport_health"]["ipv4_address"]},
            }
            if self.mutation == "health_ip":
                value[health["name"]]["IPAddress"] = "172.27.1.24"
            elif self.mutation == "health_missing":
                del value[health["name"]]
            elif self.mutation == "extra_network":
                value["extra"] = {}
            return json.dumps(value)
        return super().__call__(argv, timeout)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "models_rw",
        "models_source",
        "models_missing",
        "duplicate_mount",
        "extra_mount",
        "health_ip",
        "health_missing",
        "extra_network",
        "driver",
        "internal",
        "ipv6",
        "subnet",
        "gateway",
        "extra_ipam",
        "name",
    ],
)
def test_runtime_exact_mounts_and_isolated_health_network(tmp_path, mutation):
    plan, states, options, _, _ = inputs(tmp_path)
    manifest = compose.build_nas_compose(plan, states, runtime_layout=layout(tmp_path), **options)
    fake = LayoutDocker(manifest, mutation)
    if mutation is None:
        result = reconciler.reconcile(tmp_path / "compose.json", manifest, states, run=fake)
        assert result["running"] == ["aiport_slot_2"]
        assert result["would_start"] == []
    else:
        with pytest.raises(reconciler.ReconcileError):
            reconciler.reconcile(
                tmp_path / "compose.json",
                manifest,
                states,
                apply=True,
                run=fake,
                pause=lambda _: None,
            )
    assert not any("up" in call or "stop" in call for call in fake.calls)


def test_both_clis_load_private_explicit_runtime_layout(tmp_path, monkeypatch, capsys):
    plan, states, options, _, report = inputs(tmp_path)
    value = layout(tmp_path)
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    output = tmp_path / "compose.json"
    args = arguments(plan, states, options, tmp_path) + ["--runtime-layout", str(path)]
    assert compose.main(args + ["--output", str(output)]) == 0
    manifest = json.loads(output.read_text())
    assert manifest["services"]["aiport_slot_2"]["image"] == value["slot_images"]["2"]

    async def inventory(*a, **k):
        return report

    monkeypatch.setattr(reconciler, "fetch_inventory", inventory)
    calls = []
    monkeypatch.setattr(reconciler, "reconcile", lambda *a, **k: calls.append(True) or {})
    assert (
        reconciler.main(
            args
            + [
                "--compose",
                str(output),
                "--api-key-file",
                "fictional",
                "--web-trust-file",
                "fictional",
                "--web-cert-file",
                "fictional",
            ]
        )
        == 0
    )
    assert calls == [True]
    capsys.readouterr()


def test_mixed_images_do_not_upgrade_another_selected_slot(tmp_path):
    plan, states, options = fixture(tmp_path)
    value = layout(tmp_path)
    value["health_network"]["addresses"]["1"] = "172.27.1.21"
    manifest = compose.build_nas_compose(plan, states, runtime_layout=value, **options)
    assert manifest["services"]["aiport_slot_1"]["image"] == options["image"]
    assert manifest["services"]["aiport_slot_2"]["image"] == value["slot_images"]["2"]


@pytest.mark.parametrize("failed_health", [False, True])
def test_declared_layout_start_and_rollback_keep_exact_guards(tmp_path, failed_health):
    plan, states, options, _, _ = inputs(tmp_path)
    manifest = compose.build_nas_compose(plan, states, runtime_layout=layout(tmp_path), **options)
    fake = LayoutDocker(manifest)
    fake.started = False
    fake.fail_health = failed_health
    if failed_health:
        with pytest.raises(reconciler.ReconcileError, match="health"):
            reconciler.reconcile(
                tmp_path / "compose.json",
                manifest,
                states,
                apply=True,
                run=fake,
                pause=lambda _: None,
            )
        assert sum("stop" in call for call in fake.calls) == 1
    else:
        result = reconciler.reconcile(
            tmp_path / "compose.json", manifest, states, apply=True, run=fake, pause=lambda _: None
        )
        assert result["started"] == ["aiport_slot_2"]
        assert not any("stop" in call for call in fake.calls)
    assert sum("up" in call for call in fake.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        "true",
        "{bad",
        '{"schema":"aikey-nas-runtime-layout/1","schema":"aikey-nas-runtime-layout/1"}',
    ],
)
@pytest.mark.parametrize("entry", ["compose", "reconcile"])
def test_malformed_private_layout_file_refused_before_inventory(
    tmp_path, monkeypatch, capsys, raw, entry
):
    plan, states, options, _, _ = inputs(tmp_path)
    path = tmp_path / "layout.json"
    path.write_text(raw)
    path.chmod(0o600)
    args = arguments(plan, states, options, tmp_path) + ["--runtime-layout", str(path)]
    calls = []

    async def inventory(*a, **k):
        calls.append(True)
        raise AssertionError("inventory must not run")

    monkeypatch.setattr(reconciler, "fetch_inventory", inventory)
    with pytest.raises(SystemExit) as failure:
        if entry == "compose":
            compose.main(args + ["--output", str(tmp_path / "not-created.json")])
        else:
            reconciler.main(
                args
                + [
                    "--compose",
                    str(tmp_path / "not-read.json"),
                    "--api-key-file",
                    "fictional",
                    "--web-trust-file",
                    "fictional",
                    "--web-cert-file",
                    "fictional",
                ]
            )
    assert failure.value.code == 2 and not calls
    assert "runtime layout" in capsys.readouterr().err
    assert not (tmp_path / "not-created.json").exists()

"""Idle-gated same-host upgrade of a pinned Apple container service (#24). Synthetic host only."""

import copy
import json

import pytest

from aikey import apple_supervisor as sup
from aikey import apple_upgrade as up
from aikey.apple_upgrade import CONTAINER, UpgradeError

STATE = "/Users/me/stack/state/aiport-mac"
OLD, NEW = "aiport-old", "aiport-new"
OLD_IMAGE, NEW_IMAGE = "local-aiport:old", "local-aiport:new"
IMAGE_ENV = ["PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"]


def _entry(name=OLD, image=OLD_IMAGE, state="running"):
    """Shaped like ``container list --all --format json`` for the live Mac AI Port."""
    return {"id": name, "status": {"state": state}, "configuration": {
        "id": name, "image": {"reference": image}, "readOnly": True,
        "initProcess": {"arguments": ["--config", "/state/config.json", "--port", "8443"],
                        "environment": list(IMAGE_ENV), "executable": "local-aiport-candidate",
                        "rlimits": [], "supplementalGroups": [], "terminal": False,
                        "user": {"raw": {"userString": "10001:10001"}}, "workingDirectory": "/app"},
        "mounts": [{"destination": "/tmp", "options": ["size=64m", "mode=1777"], "source": "tmpfs",
                    "type": {"tmpfs": {}}},
                   {"destination": "/state", "options": [], "source": STATE,
                    "type": {"virtiofs": {}}}],
        "publishedPorts": [{"containerPort": 8443, "count": 1, "hostAddress": "192.168.0.135",
                            "hostPort": 8443, "proto": "tcp"}],
        "networks": [{"network": "default", "options": {"hostname": name, "mtu": 1280}}],
        "resources": {"cpuOverhead": 1, "cpus": 4, "memoryInBytes": 2 << 30},
        "dns": {"nameservers": ["192.168.0.1"], "options": [], "searchDomains": []},
        "capAdd": [], "capDrop": [], "labels": {}, "sysctls": {}, "publishedSockets": [],
        "rosetta": False, "ssh": False, "useInit": False, "virtualization": False}}


def _image(platform=("linux", "arm64"), env=IMAGE_ENV):
    return [{"variants": [{"config": {"os": platform[0], "architecture": platform[1],
                                      "config": {"Env": list(env), "WorkingDir": "/app",
                                                 "Entrypoint": ["local-aiport-candidate"]}}}]}]


class Host:
    """Fake ``container`` CLI: list, image inspect, stop, run, start, delete."""

    def __init__(self, entries, images=None, *, run_rc=0, stop_rc=0):
        self.entries = {e["id"]: e for e in entries}
        self.images = images if images is not None else {OLD_IMAGE: _image(), NEW_IMAGE: _image()}
        self.run_rc, self.stop_rc, self.calls = run_rc, stop_rc, []

    def __call__(self, argv):
        assert argv[0] == CONTAINER
        self.calls.append(argv[1:])
        verb = argv[1]
        if verb == "list":
            return 0, json.dumps(list(self.entries.values()))
        if argv[1:3] == ["image", "inspect"]:
            image = self.images.get(argv[3])
            return (0, json.dumps(image)) if image else (1, "")
        if verb == "stop":
            if self.stop_rc == 0 and argv[2] in self.entries:
                self.entries[argv[2]]["status"]["state"] = "stopped"
            return self.stop_rc, ""
        if verb == "start":
            self.entries[argv[2]]["status"]["state"] = "running"
            return 0, ""
        if verb == "delete":
            self.entries.pop(argv[2], None)
            return 0, ""
        if verb == "run":
            if self.run_rc:
                return self.run_rc, ""
            name = argv[argv.index("--name") + 1]
            self.entries[name] = recreate(argv)
            return 0, ""
        raise AssertionError(argv)

    def verbs(self):
        return [c[0] if c[0] != "image" else "inspect" for c in self.calls if c[0] != "list"]


def recreate(argv):
    """What a container started from ``argv`` would report, for round-trip checks."""
    entry = _entry(argv[argv.index("--name") + 1], argv[argv.index("--entrypoint") + 2])
    config = entry["configuration"]
    config["initProcess"]["arguments"] = argv[argv.index("--entrypoint") + 3:]
    config["initProcess"]["executable"] = argv[argv.index("--entrypoint") + 1]
    config["initProcess"]["user"]["raw"]["userString"] = argv[argv.index("--user") + 1]
    config["readOnly"] = "--read-only" in argv
    config["resources"]["cpus"] = int(argv[argv.index("--cpus") + 1])
    config["resources"]["memoryInBytes"] = int(argv[argv.index("--memory") + 1][:-1]) << 20
    config["dns"]["nameservers"] = [argv[i + 1] for i, a in enumerate(argv) if a == "--dns"]
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    volumes = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    config["mounts"] = (
        [{"destination": t.split(":")[0], "options": t.split(":")[1].split(","), "source": "tmpfs",
          "type": {"tmpfs": {}}} for t in tmpfs]
        + [{"destination": v.split(":")[1], "options": [], "source": v.split(":")[0],
            "type": {"virtiofs": {}}} for v in volumes])
    config["publishedPorts"] = [
        {"containerPort": int(p.split(":")[2]), "count": 1, "hostAddress": p.split(":")[0],
         "hostPort": int(p.split(":")[1]), "proto": "tcp"}
        for p in (argv[i + 1] for i, a in enumerate(argv) if a == "-p")]
    return entry


@pytest.fixture
def state_dir(tmp_path):
    sup.pin(tmp_path, "aiport-mac", OLD, run=Host([_entry()]))
    return tmp_path


def _spec(state_dir):
    return json.loads((state_dir / sup.SPEC_FILE).read_text())["services"]


def _comparable(entry):
    config = copy.deepcopy(entry["configuration"])
    for key in ("id", "image", "networks"):
        config.pop(key)
    return config


def test_the_plan_recreates_every_setting_of_the_live_container(state_dir):
    host = Host([_entry()])
    planned = up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=host)
    assert planned.argv == (
        CONTAINER, "run", "-d", "--name", NEW, "--user", "10001:10001", "--read-only",
        "--tmpfs", "/tmp:size=64m,mode=1777", "-v", f"{STATE}:/state",
        "--cpus", "4", "--memory", "2048M", "--dns", "192.168.0.1",
        "-p", "192.168.0.135:8443:8443", "--entrypoint", "local-aiport-candidate", NEW_IMAGE,
        "--config", "/state/config.json", "--port", "8443")
    assert _comparable(recreate(list(planned.argv))) == _comparable(_entry())
    assert [c[0] for c in host.calls] == ["list", "image", "image"]          # read-only


def test_a_different_working_directory_is_kept_explicitly(state_dir):
    entry = _entry()
    entry["configuration"]["initProcess"]["workingDirectory"] = "/srv"
    planned = up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=Host([entry]))
    assert planned.argv[planned.argv.index("--workdir") + 1] == "/srv"


def _mutate(path, value):
    def apply(entry):
        target = entry["configuration"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return apply


@pytest.mark.parametrize("change,reason", [
    (_mutate(("initProcess", "environment"), IMAGE_ENV + ["PROVIDER_TOKEN=x"]), "custom_environment"),
    (_mutate(("capAdd",), ["CAP_NET_RAW"]), "capabilities"),
    (_mutate(("labels",), {"k": "v"}), "labels"),
    (_mutate(("publishedSockets",), [{"x": 1}]), "published_sockets"),
    (_mutate(("rosetta",), True), "rosetta"),
    (_mutate(("networks",), [{"network": "default", "options": {"mtu": 9000}}]), "network"),
    (_mutate(("networks",), [{"network": "lan"}]), "network"),
    (_mutate(("dns", "searchDomains"), ["lan"]), "dns_options"),
    (_mutate(("resources", "memoryInBytes"), (2 << 30) + 1), "resources"),
    (_mutate(("initProcess", "rlimits"), [{"type": "nofile"}]), "process_options"),
    (_mutate(("mounts",), [{"destination": "/x", "source": "/y", "options": ["rw", "noexec"],
                            "type": {"virtiofs": {}}}]), "mount"),
    (_mutate(("publishedPorts",), [{"containerPort": 1, "count": 2, "hostAddress": "192.168.0.135",
                                    "hostPort": 1, "proto": "tcp"}]), "published_port"),
])
def test_a_setting_that_cannot_be_reproduced_blocks_the_plan(tmp_path, change, reason):
    entry = _entry()
    change(entry)
    host = Host([entry])
    sup.pin(tmp_path, "aiport-mac", OLD, run=host)
    with pytest.raises(UpgradeError, match=reason) as refused:
        up.plan(tmp_path, "aiport-mac", NEW, NEW_IMAGE, run=host)
    assert "PROVIDER_TOKEN" not in str(refused.value)


@pytest.mark.parametrize("setup,reason", [
    (lambda h: h.entries.update({NEW: _entry(NEW, NEW_IMAGE)}), "new_name_in_use"),
    (lambda h: h.entries[OLD]["configuration"]["image"].update(reference="local-aiport:other"),
     "pin_drift"),
    (lambda h: h.entries[OLD]["status"].update(state="stopped"), "pinned_container_not_running"),
    (lambda h: h.entries.pop(OLD), "pinned_container_missing"),
    (lambda h: h.images.pop(NEW_IMAGE), "image_unavailable"),
    (lambda h: h.images.update({NEW_IMAGE: _image(("linux", "amd64"))}), "platform_mismatch"),
])
def test_an_unsafe_starting_point_blocks_the_plan(state_dir, setup, reason):
    host = Host([_entry()])
    setup(host)
    with pytest.raises(UpgradeError, match=reason):
        up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=host)


@pytest.mark.parametrize("service,name,image,reason", [
    ("aikey-mac", NEW, NEW_IMAGE, "unknown_service"),
    ("aiport-mac", "bad name!", NEW_IMAGE, "invalid_new_name"),
    ("aiport-mac", NEW, OLD_IMAGE, "same_image"),
])
def test_bad_requests_are_refused(state_dir, service, name, image, reason):
    with pytest.raises(UpgradeError, match=reason):
        up.plan(state_dir, service, name, image, run=Host([_entry()]))


def _swap(state_dir, host, *, idle=True, ready=(True,)):
    planned = up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=host)
    host.calls.clear()
    answers = list(ready)
    clock = [0.0]

    def ready_probe():
        return answers.pop(0) if len(answers) > 1 else answers[0]

    return up.swap(state_dir, planned, idle=lambda: idle, ready=ready_probe, run=host,
                   sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0],
                   idle_timeout=10, ready_timeout=10)


def test_a_ready_replacement_is_pinned_and_the_old_container_is_kept(state_dir):
    host = Host([_entry()])
    assert _swap(state_dir, host, ready=(False, False, True)) == "swapped"
    assert host.verbs() == ["stop", "run"]
    assert host.entries[OLD]["status"]["state"] == "stopped"          # kept for rollback
    assert host.entries[NEW]["status"]["state"] == "running"
    assert _spec(state_dir)[0]["container"] == NEW and _spec(state_dir)[0]["image"] == NEW_IMAGE
    assert _spec(state_dir)[0]["state_source"] == STATE
    runtime = json.loads((state_dir / sup.RUNTIME_FILE).read_text())
    assert "aiport-mac" not in runtime.get("holds", {})                # hold released


def test_a_busy_service_is_not_touched(state_dir):
    host = Host([_entry()])
    before = (state_dir / sup.SPEC_FILE).read_bytes()
    assert _swap(state_dir, host, idle=False) == "not_idle"
    assert host.verbs() == [] and (state_dir / sup.SPEC_FILE).read_bytes() == before


def test_a_replacement_that_never_becomes_ready_is_rolled_back(state_dir):
    host = Host([_entry()])
    ready = iter([False] * 6 + [True] * 10)                           # old ready again after restart
    planned = up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=host)
    host.calls.clear()
    clock = [0.0]
    result = up.swap(state_dir, planned, idle=lambda: True, ready=lambda: next(ready), run=host,
                     sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0],
                     idle_timeout=10, ready_timeout=10)
    assert result == "rolled_back:not_ready"
    assert host.verbs() == ["stop", "run", "stop", "delete", "start"]
    assert NEW not in host.entries and host.entries[OLD]["status"]["state"] == "running"
    assert _spec(state_dir)[0]["container"] == OLD


def test_a_failed_start_restores_the_old_container(state_dir):
    host = Host([_entry()], run_rc=1)
    assert _swap(state_dir, host) == "rolled_back:run_failed"
    assert host.entries[OLD]["status"]["state"] == "running" and NEW not in host.entries
    assert _spec(state_dir)[0]["container"] == OLD


def test_a_failed_rollback_is_reported_and_the_old_pin_restored(state_dir):
    host = Host([_entry()])
    assert _swap(state_dir, host, ready=(False,)) == "rollback_not_ready"
    assert _spec(state_dir)[0]["container"] == OLD


def test_a_failed_stop_changes_nothing_and_releases_the_hold(state_dir):
    host = Host([_entry()], stop_rc=1)
    assert _swap(state_dir, host) == "stop_failed"
    assert host.verbs() == ["stop"] and _spec(state_dir)[0]["container"] == OLD
    runtime = json.loads((state_dir / sup.RUNTIME_FILE).read_text())
    assert "aiport-mac" not in runtime.get("holds", {})


def test_a_probe_error_counts_as_not_ready(state_dir):
    host = Host([_entry()])

    def broken():
        raise OSError("private detail")
    planned = up.plan(state_dir, "aiport-mac", NEW, NEW_IMAGE, run=host)
    clock = [0.0]
    assert up.swap(state_dir, planned, idle=broken, ready=broken, run=host,
                   sleep=lambda s: clock.__setitem__(0, clock[0] + s), clock=lambda: clock[0],
                   idle_timeout=5) == "not_idle"


@pytest.mark.parametrize("health,idle,ready", [
    ({"adopted": True, "control_connected": True, "active_streams": 3,
      "streams_with_decoded_frames": 3, "smart_events_entered": 4, "smart_events_left": 4}, True, True),
    ({"adopted": True, "control_connected": True, "active_streams": 3,
      "streams_with_decoded_frames": 2, "smart_events_entered": 5, "smart_events_left": 4}, False, False),
    ({"adopted": False, "control_connected": False}, False, False),
    ({}, False, False),
])
def test_ai_port_idle_and_ready_predicates(health, idle, ready):
    assert up.aiport_idle(health) is idle and up.aiport_ready(health, 3) is ready


def test_ai_key_idle_and_ready_predicates():
    good = {"worker": {"queued": 0, "active": 0, "pending": 0},
            "device": {"adopted": True, "connected": True}, "search": {"connected": True}}
    assert up.aikey_idle(good) and up.aikey_ready(good)
    assert not up.aikey_idle({"worker": {"queued": 1, "active": 0, "pending": 0}})
    assert not up.aikey_idle({})
    assert not up.aikey_ready({"device": {"adopted": True, "connected": True}, "search": {}})


def test_cli_plan_prints_names_and_flags_only(state_dir, monkeypatch, capsys):
    monkeypatch.setattr(up, "_run", Host([_entry()]))
    monkeypatch.setattr(up.plan, "__defaults__", (up._run,))
    assert up.main(["plan", "--state-dir", str(state_dir), "--service", "aiport-mac",
                    "--new-name", NEW, "--image", NEW_IMAGE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["old"] == OLD and out["new"] == NEW and "--read-only" in out["run_flags"]
    assert "--config" not in out["run_flags"]                               # app arguments are not flags
    assert STATE not in json.dumps(out) and "config.json" not in json.dumps(out)
    assert up.main(["swap", "--state-dir", str(state_dir), "--service", "aiport-mac",
                    "--new-name", NEW, "--image", NEW_IMAGE]) == 2          # no --kind: refused

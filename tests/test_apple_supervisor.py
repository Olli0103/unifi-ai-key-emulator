"""Synthetic container listings for the Mac service supervisor."""

import json
import plistlib

import pytest

from aikey import apple_supervisor as sup


STATE = "/Users/me/stack/state/aiport-mac"


def _entry(name, state, *, image="local-aiport:new", address="192.168.0.135", port=8443,
           source=STATE):
    return {"id": name, "status": {"state": state},
            "configuration": {"image": {"reference": image},
                              "publishedPorts": [{"hostAddress": address, "hostPort": port,
                                                  "containerPort": port}],
                              "mounts": [{"destination": "/tmp", "source": "tmpfs"},
                                         {"destination": "/state", "source": source}]}}


class Host:
    """Fake ``container`` CLI and ``ifconfig``."""

    def __init__(self, listing, *, system="running", addresses=("192.168.0.135",),
                 start_rc=0):
        self.listing, self.system, self.addresses = listing, system, addresses
        self.start_rc, self.calls = start_rc, []

    def __call__(self, argv):
        self.calls.append(argv[1:])
        if argv[0] == "/sbin/ifconfig":
            return 0, "".join(f"\tinet {a} netmask 0xffffff00\n" for a in self.addresses)
        if argv[1:3] == ["system", "status"]:
            return (0, f"FIELD VALUE\nstatus              {self.system}\n")
        if argv[1:3] == ["system", "start"]:
            self.system = "running"
            return 0, ""
        if argv[1] == "list":
            return 0, json.dumps(self.listing)
        if argv[1] == "start":
            if self.start_rc == 0:
                for item in self.listing:
                    if item["id"] == argv[2]:
                        item["status"]["state"] = "running"
            return self.start_rc, ""
        raise AssertionError(argv)

    def starts(self):
        return [c for c in self.calls if c[:1] == ["start"]]


@pytest.fixture
def state_dir(tmp_path):
    host = Host([_entry("aiport-new", "running")])
    sup.pin(tmp_path, "aiport-mac", "aiport-new", run=host)
    return tmp_path


def test_pin_records_the_live_configuration_privately(state_dir):
    spec = json.loads((state_dir / sup.SPEC_FILE).read_text())
    assert spec["services"] == [{"name": "aiport-mac", "container": "aiport-new",
                                 "image": "local-aiport:new", "host_address": "192.168.0.135",
                                 "host_port": 8443, "state_source": STATE}]
    assert (state_dir / sup.SPEC_FILE).stat().st_mode & 0o077 == 0


def test_a_running_service_is_left_alone(state_dir):
    host = Host([_entry("aiport-new", "running")])
    assert sup.tick(state_dir, run=host, now=1000.0)["services"] == {"aiport-mac": "running"}
    assert host.starts() == []


def test_after_a_reboot_the_system_and_the_pinned_container_are_started(state_dir):
    host = Host([_entry("aiport-new", "stopped")], system="stopped")
    report = sup.tick(state_dir, run=host, now=1000.0)
    assert report == {"system": "started", "services": {"aiport-mac": "started"}}
    assert ["system", "start", "--disable-kernel-install", "--timeout", "60"] in host.calls
    assert host.starts() == [["start", "aiport-new"]]


def test_an_older_stopped_container_of_the_same_identity_is_never_started(state_dir):
    host = Host([_entry("aiport-old", "stopped", image="local-aiport:old"),
                 _entry("aiport-new", "stopped")])
    sup.tick(state_dir, run=host, now=1000.0)
    assert host.starts() == [["start", "aiport-new"]]


def test_another_running_container_on_the_same_port_or_state_blocks_the_start(state_dir):
    for other in (_entry("swap", "running", image="local-aiport:next"),   # both
                  _entry("swap", "running", port=9443),                   # same state
                  _entry("swap", "running", source="/elsewhere")):        # same port
        host = Host([other, _entry("aiport-new", "stopped")])
        assert sup.tick(state_dir, run=host, now=1000.0)["services"]["aiport-mac"] == \
            "blocked:conflict"
        assert host.starts() == []
    host = Host([_entry("swap", "running", port=9443, source="/elsewhere"),
                 _entry("aiport-new", "stopped")])
    assert sup.tick(state_dir, run=host, now=1000.0)["services"]["aiport-mac"] == "started"


def test_a_recreated_container_with_different_settings_is_drift(state_dir):
    for changed in (_entry("aiport-new", "stopped", image="local-aiport:other"),
                    _entry("aiport-new", "stopped", address="192.168.0.99"),
                    _entry("aiport-new", "stopped", source="/other/state")):
        host = Host([changed])
        assert sup.tick(state_dir, run=host, now=1000.0)["services"]["aiport-mac"] == \
            "blocked:drift"
        assert host.starts() == []


def test_missing_container_and_absent_host_address_are_reported(state_dir):
    assert sup.tick(state_dir, run=Host([]), now=1.0)["services"] == {
        "aiport-mac": "blocked:missing"}
    host = Host([_entry("aiport-new", "stopped")], addresses=("192.168.0.98",))
    assert sup.tick(state_dir, run=host, now=1.0)["services"] == {
        "aiport-mac": "blocked:address_absent"}
    assert host.starts() == []


def test_a_hold_for_a_manual_redeploy_is_respected_and_expires(state_dir):
    sup.hold(state_dir, "aiport-mac", 20, now=1000.0)
    host = Host([_entry("aiport-new", "stopped")])
    assert sup.tick(state_dir, run=host, now=1000.0 + 60)["services"]["aiport-mac"] == "held"
    assert sup.tick(state_dir, run=host, now=1000.0 + 21 * 60)["services"]["aiport-mac"] == \
        "started"


def test_repinning_after_a_redeploy_moves_to_the_new_container(state_dir):
    sup.hold(state_dir, "aiport-mac", 20, now=1000.0)
    listing = [_entry("aiport-new", "stopped"),
               _entry("aiport-next", "running", image="local-aiport:next")]
    sup.pin(state_dir, "aiport-mac", "aiport-next", run=Host(listing))
    host = Host(listing)
    assert sup.tick(state_dir, run=host, now=1100.0)["services"] == {"aiport-mac": "running"}
    assert host.starts() == []


def test_a_crash_loop_stops_after_the_hourly_start_limit(state_dir):
    for minute in range(3):
        host = Host([_entry("aiport-new", "stopped")], start_rc=0)
        host.listing[0]["status"]["state"] = "stopped"
        assert sup.tick(state_dir, run=host, now=1000.0 + minute * 60)["services"][
            "aiport-mac"] == "started"
    host = Host([_entry("aiport-new", "stopped")])
    assert sup.tick(state_dir, run=host, now=1000.0 + 240)["services"]["aiport-mac"] == \
        "blocked:crash_loop"
    assert sup.tick(state_dir, run=host, now=1000.0 + 3700)["services"]["aiport-mac"] == \
        "started"


def test_a_failed_start_is_reported_and_counted(state_dir):
    host = Host([_entry("aiport-new", "stopped")], start_rc=1)
    assert sup.tick(state_dir, run=host, now=1.0)["services"]["aiport-mac"] == \
        "blocked:start_failed"
    runtime = json.loads((state_dir / sup.RUNTIME_FILE).read_text())
    assert len(runtime["starts"]["aiport-mac"]) == 1


def test_an_unavailable_container_system_starts_nothing(state_dir):
    class Down(Host):
        def __call__(self, argv):
            if argv[1:3] == ["system", "start"]:
                self.calls.append(argv[1:])
                return 1, ""
            return super().__call__(argv)
    host = Down([_entry("aiport-new", "stopped")], system="stopped")
    assert sup.tick(state_dir, run=host, now=1.0) == {"system": "unavailable", "services": {}}
    assert host.starts() == []


def test_the_spec_is_validated():
    with pytest.raises(sup.SupervisorError):
        sup._service({"name": "x", "container": "c", "image": "i", "host_address": "1.2.3.4",
                      "host_port": 1, "state_source": "relative/path"})
    with pytest.raises(ValueError):
        sup._service({"name": "x", "container": "c", "image": "i", "host_address": "nope",
                      "host_port": 1, "state_source": "/s"})


def test_the_launch_agent_runs_one_tick_a_minute(tmp_path):
    agent = plistlib.loads(sup.launch_agent(tmp_path, "/venv/bin/python").encode())
    assert agent["ProgramArguments"] == ["/venv/bin/python", "-m", "aikey.apple_supervisor",
                                         "tick", "--state-dir", str(tmp_path)]
    assert agent["StartInterval"] == 60 and agent["RunAtLoad"] is True
    assert "KeepAlive" not in agent

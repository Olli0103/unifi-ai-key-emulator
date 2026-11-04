"""Typed durable intent is a fence, not a command acknowledgement or retry permit."""

from copy import deepcopy
import json
import os

import pytest

from aikey import apple_supervisor as supervisor
from aikey import apple_upgrade as upgrade
from aikey import apple_upgrade_state as state
from aikey.host_control_store import writer
from test_apple_upgrade import Host, _entry, OLD, NEW, NEW_IMAGE


def prepared(tmp_path):
    host = Host([_entry()])
    supervisor.pin(tmp_path, "aiport-mac", OLD, run=host)
    planned = upgrade.plan(tmp_path, "aiport-mac", NEW, NEW_IMAGE, run=host)
    raw = (tmp_path / supervisor.SPEC_FILE).read_bytes()
    with writer(tmp_path) as (_, bound):
        value = state.prepare(tmp_path, planned, raw, bound)
    return value, planned, raw


def test_prepared_receipt_is_private_durable_and_fences_another_attempt(tmp_path):
    value, planned, raw = prepared(tmp_path)
    assert state.pending(tmp_path) == value and state.read(tmp_path) == value
    assert (tmp_path / state.FILE).stat().st_mode & 0o777 == 0o600
    before = (tmp_path / state.FILE).read_bytes()
    with writer(tmp_path) as (_, bound):
        with pytest.raises(state.UpgradeStateError, match="original_recovery"):
            state.prepare(tmp_path, planned, raw, bound)
    assert (tmp_path / state.FILE).read_bytes() == before


def test_acknowledgement_is_not_terminal_evidence(tmp_path):
    value, _, _ = prepared(tmp_path)
    with writer(tmp_path) as (_, bound):
        state.transition(tmp_path, value, "stop_old", "requested", bound)
        state.transition(tmp_path, value, "stop_old", "acknowledged", bound)
        with pytest.raises(state.UpgradeStateError):
            state.finish(tmp_path, value, "rolled_back", None, bound)
    saved = state.read(tmp_path)
    assert saved["phase"] == "switching" and saved["actions"][0]["state"] == "acknowledged"


def test_next_attempt_preserves_exact_terminal_predecessor(tmp_path):
    value, planned, raw = prepared(tmp_path)
    with writer(tmp_path) as (_, bound):
        for action in ("hold", "stop_old", "restart_old", "ready_old", "pin_old", "new_absent"):
            for phase in ("requested", "acknowledged", "observed"):
                state.transition(tmp_path, value, action, phase, bound)
        state.finish(tmp_path, value, "rolled_back", "operation_failed", bound)
        before = (tmp_path / state.FILE).read_bytes()
        next_value = state.prepare(tmp_path, planned, raw, bound)
    assert next_value["operation"] != value["operation"]
    assert (tmp_path / "upgrade-history" / (value["operation"] + ".json")).read_bytes() == before
    assert state.pending(tmp_path) == next_value


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_field",
        "schema",
        "phase",
        "operation",
        "plan",
        "argv",
        "same_names",
        "actions",
        "duplicate_actions",
        "terminal_ack",
        "failure",
    ],
)
def test_structurally_ambiguous_receipts_never_unlock_recovery(tmp_path, fault):
    value, _, _ = prepared(tmp_path)
    if fault == "unknown_field":
        value["untrusted"] = "synthetic-canary"
    if fault == "schema":
        value["schema"] = "other/0"
    if fault == "phase":
        value["phase"] = "ignored"
    if fault == "operation":
        value["operation"] = "../other"
    if fault == "plan":
        value["plan"]["opaque"] = "synthetic-canary"
    if fault == "argv":
        value["plan"]["argv"][0] = "/bin/fictional-other"
    if fault == "same_names":
        value["plan"]["new"] = value["plan"]["old"]
    if fault == "actions":
        value["actions"] = [{"action": "run_arbitrary", "state": "observed"}]
    if fault == "duplicate_actions":
        value["actions"] = [{"action": "stop_old", "state": "requested"}] * 2
    if fault == "terminal_ack":
        value["phase"] = "rolled_back"
        value["actions"] = [{"action": "stop_old", "state": "acknowledged"}]
    if fault == "failure":
        value["failure"] = "synthetic-private-error-payload"
    (tmp_path / state.FILE).write_text(json.dumps(value))
    with pytest.raises(state.UpgradeStateError, match="unavailable"):
        state.pending(tmp_path)


@pytest.mark.parametrize(
    "fault", ["fifo", "symlink", "hardlink", "public", "oversize", "duplicate_json"]
)
def test_unsafe_file_is_not_a_missing_intent(tmp_path, fault):
    path = tmp_path / state.FILE
    if fault == "fifo":
        os.mkfifo(path, 0o600)
    elif fault == "symlink":
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_text(
            '{"x":1,"x":2}'
            if fault == "duplicate_json"
            else "x" * (state.LIMIT + 1)
            if fault == "oversize"
            else "{}"
        )
        path.chmod(0o600)
        if fault == "hardlink":
            os.link(path, tmp_path / "second-link")
        if fault == "public":
            path.chmod(0o644)
    with pytest.raises(state.UpgradeStateError):
        state.pending(tmp_path)


def test_different_pending_owner_cannot_be_overwritten(tmp_path):
    value, _, _ = prepared(tmp_path)
    other = deepcopy(value)
    other["operation"] = "0" * 32
    before = (tmp_path / state.FILE).read_bytes()
    with writer(tmp_path) as (_, bound):
        with pytest.raises(state.UpgradeStateError, match="owned_elsewhere"):
            state.publish(tmp_path, other, bound)
    assert (tmp_path / state.FILE).read_bytes() == before


def test_supervisor_cannot_restart_or_clear_an_expired_hold_while_intent_is_pending(tmp_path):
    value, _, _ = prepared(tmp_path)
    host = Host([_entry(state="stopped")])
    report = supervisor.tick(tmp_path, run=host, now=10**12)
    assert report == {
        "system": "upgrade_pending",
        "services": {"aiport-mac": "blocked:upgrade_pending"},
    }
    assert host.calls == []  # Not even a kernel/system-start request.
    assert state.pending(tmp_path) == value
    for call in (
        lambda: supervisor.release(tmp_path, "aiport-mac"),
        lambda: supervisor.hold(tmp_path, "aiport-mac", 20),
        lambda: supervisor.pin(tmp_path, "aiport-mac", OLD, run=host),
    ):
        with pytest.raises(supervisor.SupervisorError, match="original_recovery"):
            call()
    assert state.pending(tmp_path) == value


def test_a_guest_mounted_authority_is_refused_before_lock_or_journal_creation(tmp_path):
    guest = tmp_path / "guest"
    guest.mkdir(mode=0o700)
    control = guest / "supervisor"
    host = Host([_entry()])
    host.entries[OLD]["configuration"]["mounts"][1]["source"] = str(guest)
    with pytest.raises(supervisor.SupervisorError, match="outside_guest"):
        supervisor.pin(control, "aiport-mac", OLD, run=host)
    assert not control.exists()


def test_unsafe_current_intent_fences_tick_before_any_host_action(tmp_path):
    host = Host([_entry()])
    supervisor.pin(tmp_path, "aiport-mac", OLD, run=host)
    (tmp_path / state.FILE).symlink_to(tmp_path / "missing-intent")
    host.calls.clear()
    with pytest.raises(state.UpgradeStateError):
        supervisor.tick(tmp_path, run=host)
    assert host.calls == []


def test_copied_intent_is_not_authority_for_another_control_root(tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    prepared(original)
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    (other / state.FILE).write_bytes((original / state.FILE).read_bytes())
    (other / state.FILE).chmod(0o600)
    with pytest.raises(state.UpgradeStateError, match="unavailable"):
        state.read(other)

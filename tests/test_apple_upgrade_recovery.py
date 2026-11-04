"""Request ACK is not an observed Apple runtime transition. Invented host only."""

import json

import pytest

from aikey import apple_upgrade as up
from aikey import apple_supervisor as sup
from test_apple_upgrade import Host, _entry, OLD, NEW, NEW_IMAGE


class IgnoredTransition(Host):
    def __init__(self, fault):
        super().__init__([_entry()])
        self.fault = fault

    def __call__(self, argv):
        transition = tuple(argv[1:])
        ignored = {
            "old_stop_ack_only": ("stop", OLD),
            "new_stop_ack_only": ("stop", NEW),
            "new_stop_failed": ("stop", NEW),
            "new_delete_ack_only": ("delete", NEW),
            "old_restart_ack_only": ("start", OLD),
        }
        if transition == ignored[self.fault]:
            self.calls.append(argv[1:])
            return (1 if self.fault == "new_stop_failed" else 0), ""
        return super().__call__(argv)


def run_swap(tmp_path, host, *, negative_new_readiness=False):
    sup.pin(tmp_path, "aiport-mac", OLD, run=host)
    planned = up.plan(tmp_path, "aiport-mac", NEW, NEW_IMAGE, run=host)
    host.calls.clear()
    answers = [False] * 6 + [True] * 10 if negative_new_readiness else [True] * 10
    clock = [0.0]
    result = up.swap(
        tmp_path,
        planned,
        idle=lambda: True,
        ready=lambda: answers.pop(0),
        run=host,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        clock=lambda: clock[0],
        idle_timeout=10,
        ready_timeout=10,
    )
    return result


@pytest.mark.parametrize("fault", ["new_stop_ack_only", "new_stop_failed", "new_delete_ack_only"])
def test_unobserved_new_compensation_never_starts_old_identity(tmp_path, fault):
    host = IgnoredTransition(fault)
    result = run_swap(tmp_path, host, negative_new_readiness=True)
    assert result == "rollback_outcome_uncertain"
    assert ["start", OLD] not in host.calls
    if fault != "new_delete_ack_only":
        assert ["delete", NEW] not in host.calls
    assert "aiport-mac" in json.loads((tmp_path / sup.RUNTIME_FILE).read_text())["holds"]


def test_unobserved_old_stop_cannot_admit_new_identity(tmp_path):
    host = IgnoredTransition("old_stop_ack_only")
    result = run_swap(tmp_path, host)
    assert result == "stop_outcome_uncertain"
    assert not any(call[0] == "run" for call in host.calls)
    assert NEW not in host.entries


def test_unobserved_old_restart_cannot_report_successful_rollback(tmp_path):
    host = IgnoredTransition("old_restart_ack_only")
    result = run_swap(tmp_path, host, negative_new_readiness=True)
    assert result == "rollback_outcome_uncertain"
    assert host.entries[OLD]["status"]["state"] == "stopped"
    assert "aiport-mac" in json.loads((tmp_path / sup.RUNTIME_FILE).read_text())["holds"]


def test_actual_actor_persists_requested_transition_before_the_host_command(tmp_path):
    from aikey import apple_upgrade_state as journal

    class InterruptedHost(Host):
        def __call__(self, argv):
            if argv[1:2] == ["run"]:
                value = journal.read(tmp_path)
                assert value["actions"][-1] == {"action": "run_new", "state": "requested"}
                assert any(
                    row == {"action": "stop_old", "state": "observed"} for row in value["actions"]
                )
                raise KeyboardInterrupt("synthetic interruption before backend run")
            return super().__call__(argv)

    host = InterruptedHost([_entry()])
    with pytest.raises(KeyboardInterrupt):
        run_swap(tmp_path, host)
    assert host.entries[OLD]["status"]["state"] == "stopped"
    assert NEW not in host.entries
    saved = (tmp_path / journal.FILE).read_bytes()
    host.calls.clear()
    assert sup.tick(tmp_path, run=host, now=10**12)["system"] == "upgrade_pending"
    assert host.calls == [] and (tmp_path / journal.FILE).read_bytes() == saved


def test_a_successful_actor_commits_only_observed_steps_and_no_recovery_fence(tmp_path):
    from aikey import apple_upgrade_state as journal

    host = Host([_entry()])
    assert run_swap(tmp_path, host) == "swapped"
    value = journal.read(tmp_path)
    assert value["phase"] == "committed" and journal.pending(tmp_path) is None
    assert all(row["state"] == "observed" for row in value["actions"])
    assert {row["action"] for row in value["actions"]} == {
        "hold",
        "stop_old",
        "run_new",
        "ready_new",
        "pin_new",
    }


def test_explicit_recovery_of_failed_compensation_preserves_original_failure(tmp_path):
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    host = IgnoredTransition('new_stop_failed')
    assert run_swap(tmp_path, host, negative_new_readiness=True) == 'rollback_outcome_uncertain'
    before = journal.read(tmp_path)
    # Lost/nonzero stop has no affirmative no-spawn/terminal-success note.
    assert recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host) == 'recovery_command_unsettled'
    assert journal.read(tmp_path) == before
    assert ['start', OLD] not in host.calls


def test_recovery_after_interruption_at_readiness_removes_only_owned_candidate(tmp_path):
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    host = Host([_entry()])
    sup.pin(tmp_path, 'aiport-mac', OLD, run=host)
    planned = up.plan(tmp_path, 'aiport-mac', NEW, NEW_IMAGE, run=host)
    def interrupted():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        up.swap(tmp_path, planned, idle=lambda: True, ready=interrupted, run=host)
    original = journal.read(tmp_path)
    assert original['actions'][-1] == {'action': 'ready_new', 'state': 'requested'}
    assert recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host) == 'recovered_old'
    value = journal.read(tmp_path)
    assert value['phase'] == 'reconciled' and journal.pending(tmp_path) is None
    assert value['actions'] == original['actions']  # No fabricated original ACKs.
    assert value['failure'] == 'interrupted_or_unsettled'
    assert value['reconciliation']['guest_state_restored'] is False
    assert all(row['state'] == 'observed' for row in value['recovery_actions'])
    assert NEW not in host.entries and host.entries[OLD]['status']['state'] == 'running'
    assert recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host) == 'no_pending_upgrade'


@pytest.mark.parametrize('foreign', ['label', 'old_configuration', 'registry', 'service'])
def test_recovery_refuses_drift_without_vm_mutation(tmp_path, foreign):
    from aikey.apple_upgrade_recovery import recover
    host = Host([_entry()])
    sup.pin(tmp_path, 'aiport-mac', OLD, run=host)
    planned = up.plan(tmp_path, 'aiport-mac', NEW, NEW_IMAGE, run=host)
    def interrupted():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        up.swap(tmp_path, planned, idle=lambda: True, ready=interrupted, run=host)
    if foreign == 'label':
        host.entries[NEW]['configuration']['labels'] = {}
    elif foreign == 'old_configuration':
        host.entries[OLD]['configuration']['dns']['nameservers'] = ['10.77.0.2']
    elif foreign == 'registry':
        value = json.loads((tmp_path / sup.SPEC_FILE).read_text())
        value['max_starts_per_hour'] = 4
        (tmp_path / sup.SPEC_FILE).write_text(json.dumps(value))
    host.calls.clear()
    result = recover(tmp_path, 'other' if foreign == 'service' else 'aiport-mac', ready=lambda: True, run=host)
    assert result.startswith('recovery_') and result != 'recovered_old'
    assert all(call[0] == 'list' for call in host.calls)


def test_a_late_positive_probe_cannot_extend_the_wait_budget():
    clock = [0.0]
    def late():
        clock[0] = 11
        return True
    assert up._wait(late, 10, lambda _: None, lambda: clock[0], 2) is False


@pytest.mark.parametrize('fault', ['missing', 'no_spawn', 'wrong_digest', 'spawn_failed'])
def test_requested_run_needs_exact_terminal_command_evidence_before_recovery(tmp_path, fault):
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover, _argv, _context
    from aikey.host_runtime_command import publish_effect
    from aikey.host_control_store import writer
    from test_apple_upgrade_state import prepared
    value, _, _ = prepared(tmp_path)
    host = Host([_entry(state='stopped')])
    with writer(tmp_path) as (_, bound):
        for action in ('hold', 'stop_old'):
            for state in ('requested', 'acknowledged', 'observed'):
                journal.transition(tmp_path, value, action, state, bound)
        journal.transition(tmp_path, value, 'run_new', 'requested', bound)
        if fault != 'missing':
            context = _context(value, 'run_new', _argv(value, 'run_new'))
            if fault == 'wrong_digest':
                context['argv_sha256'] = 'e' * 64
            result = {'spawned': fault == 'spawn_failed', 'command_terminal': True,
                      'exit_code': 1 if fault == 'spawn_failed' else None,
                      'error': 'owner_lost' if fault != 'spawn_failed' else None}
            publish_effect(bound.root_fd, context, result)
    result = recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host)
    if fault == 'no_spawn':
        assert result == 'recovered_old'
        assert host.entries[OLD]['status']['state'] == 'running'
        assert journal.read(tmp_path)['actions'][-1] == {'action': 'run_new', 'state': 'requested'}
    else:
        assert result in {'recovery_command_unsettled', 'recovery_outcome_uncertain'}
        assert ['start', OLD] not in host.calls
        assert journal.pending(tmp_path) is not None


def test_failed_readiness_remains_fenced_and_recovery_attempts_are_bounded(tmp_path):
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    from test_apple_upgrade_state import prepared
    prepared(tmp_path)  # Before the first native request: unambiguous no RPC.
    host = Host([_entry()])
    for attempt in range(1, 4):
        clock = [0.0]
        assert recover(tmp_path, 'aiport-mac', ready=lambda: False, run=host,
                       ready_timeout=1, clock=lambda: clock[0],
                       sleep=lambda s: clock.__setitem__(0, clock[0] + s)) == 'recovery_not_ready'
        assert journal.pending(tmp_path)['recovery_attempts'] == attempt
    assert recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host) == 'recovery_outcome_uncertain'
    assert journal.pending(tmp_path)['recovery_attempts'] == 3


@pytest.mark.parametrize('field', ['environment', 'working_directory'])
def test_changed_image_defaults_need_an_explicit_migration_not_a_silent_swap(tmp_path, field):
    host = Host([_entry()])
    config = host.images[NEW_IMAGE][0]['variants'][0]['config']['config']
    config['Env' if field == 'environment' else 'WorkingDir'] = ['OTHER=1'] if field == 'environment' else '/different'
    sup.pin(tmp_path, 'aiport-mac', OLD, run=host)
    with pytest.raises(up.UpgradeError, match='defaults_migration'):
        up.plan(tmp_path, 'aiport-mac', NEW, NEW_IMAGE, run=host)
    assert all(call[0] in {'list', 'image'} for call in host.calls)


@pytest.mark.parametrize('action', ['stop_old', 'run_new', 'stop_new', 'delete_new', 'restart_old'])
@pytest.mark.parametrize('saved_note', [False, True])
def test_each_native_effect_cutpoint_needs_its_durable_note_before_rollback(tmp_path, action, saved_note):
    import os
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    from aikey.host_runtime_command import publish_effect
    host = Host([_entry()])
    sup.pin(tmp_path, 'aiport-mac', OLD, run=host)
    planned = up.plan(tmp_path, 'aiport-mac', NEW, NEW_IMAGE, run=host)
    def effect(argv, context):
        result = host(argv)
        if saved_note or context['action'] != action:
            root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                publish_effect(root, context, {'spawned': True, 'command_terminal': True,
                                               'exit_code': result[0], 'error': None})
            finally:
                os.close(root)
        if context['action'] == action:
            raise KeyboardInterrupt('synthetic cut after CLI effect before actor ACK')
        return result
    host.with_effect = effect
    clock = [0.0]
    with pytest.raises(KeyboardInterrupt):
        up.swap(tmp_path, planned, idle=lambda: True,
                ready=lambda: action in {'stop_old', 'run_new'}, run=host,
                ready_timeout=1, clock=lambda: clock[0],
                sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    original = journal.pending(tmp_path)
    assert next(row for row in original['actions'] if row['action'] == action)['state'] == 'requested'
    del host.with_effect  # Explicit recovery uses the same invented backend, not interrupted adapter.
    host.calls.clear()
    result = recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host)
    if saved_note:
        assert result == 'recovered_old'
        assert NEW not in host.entries and host.entries[OLD]['status']['state'] == 'running'
        assert journal.read(tmp_path)['actions'] == original['actions']
    else:
        assert result == 'recovery_command_unsettled'
        assert host.calls == []  # No extra daemon request when the effect has no exact evidence.
        assert journal.pending(tmp_path) == original


def test_recovery_after_new_pin_before_terminal_journal_restores_original_pin(tmp_path, monkeypatch):
    from aikey import apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    host = Host([_entry()])
    sup.pin(tmp_path, 'aiport-mac', OLD, run=host)
    planned = up.plan(tmp_path, 'aiport-mac', NEW, NEW_IMAGE, run=host)
    publish = journal.publish
    def interrupted(directory, value, bound):
        publish(directory, value, bound)
        if value['phase'] == 'switching' and value['actions'][-1] == {'action': 'pin_new', 'state': 'observed'}:
            raise KeyboardInterrupt()
    monkeypatch.setattr(journal, 'publish', interrupted)
    with pytest.raises(KeyboardInterrupt):
        up.swap(tmp_path, planned, idle=lambda: True, ready=lambda: True, run=host)
    assert sup.load_spec(tmp_path / sup.SPEC_FILE)[0][0].container == NEW
    monkeypatch.setattr(journal, 'publish', publish)
    assert recover(tmp_path, 'aiport-mac', ready=lambda: True, run=host) == 'recovered_old'
    assert sup.load_spec(tmp_path / sup.SPEC_FILE)[0][0].container == OLD


def test_status_cli_never_uses_a_daemon_and_cannot_select_a_different_image(tmp_path, capsys):
    from test_apple_upgrade_state import prepared
    prepared(tmp_path)
    assert up.main(['status', '--state-dir', str(tmp_path), '--service', 'aiport-mac']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['phase'] == 'prepared' and value['recovery_required'] is True
    assert str(tmp_path) not in json.dumps(value)
    assert up.main(['recover', '--state-dir', str(tmp_path), '--service', 'aiport-mac', '--image', NEW_IMAGE]) == 2
    assert 'recovery_uses_original_intent' in capsys.readouterr().err


@pytest.mark.parametrize('change_after_crash', [False, True])
def test_upgrade_reserves_actual_key_configuration_until_original_reconciliation(tmp_path, change_after_crash):
    import hashlib
    from aikey.config import initialize
    from aikey.config_store import ConfigurationStore, UnsafeConfigurationChange
    from aikey import host_activation, apple_upgrade_state as journal
    from aikey.apple_upgrade_recovery import recover
    guest, control = tmp_path / 'guest', tmp_path / 'control'
    guest.mkdir(mode=0o700)
    config = guest / 'config.json'
    initialize(config, guest, controller_host='127.0.0.1', device_ip='127.0.0.1')
    entry = _entry()
    entry['configuration']['mounts'][1]['source'] = str(guest)
    entry['configuration']['initProcess']['executable'] = 'local-aikey'
    host = Host([entry])
    sup.pin(control, 'key-mac', OLD, run=host)
    planned = up.plan(control, 'key-mac', NEW, NEW_IMAGE, run=host)
    before = config.read_bytes()
    def interrupted():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        up.swap(control, planned, idle=lambda: True, ready=interrupted, run=host, configuration=config)
    pending = journal.pending(control)
    assert pending['configuration_revision'] == hashlib.sha256(before).hexdigest()
    reservation = host_activation.read(config)
    assert reservation['operation'] == pending['operation'] and reservation['phase'] == 'prepared'
    with pytest.raises(UnsafeConfigurationChange, match='Pending host activation'):
        with ConfigurationStore(config)._locked():
            pytest.fail('ordinary writer entered a pending upgrade')
    if change_after_crash:
        # Unsupported manual mutation, not an authorized ConfigStore operation.
        config.write_bytes(before + b'\n')
        host.calls.clear()
        assert recover(control, 'key-mac', ready=lambda: True, run=host) == 'recovery_configuration_changed'
        assert host.calls == [] and journal.pending(control) is not None
        assert host_activation.read(config) == reservation
    else:
        assert recover(control, 'key-mac', ready=lambda: True, run=host,
                       expected_operation=pending['operation']) == 'recovered_old'
        assert config.read_bytes() == before and host_activation.read(config)['phase'] == 'settled'
        with ConfigurationStore(config)._locked():
            pass


def test_native_swap_cannot_omit_the_configuration_reservation(tmp_path):
    from test_apple_upgrade_state import prepared
    _, planned, _ = prepared(tmp_path)
    assert up.swap(tmp_path, planned, idle=lambda: True, ready=lambda: True) == 'configuration_reservation_required'

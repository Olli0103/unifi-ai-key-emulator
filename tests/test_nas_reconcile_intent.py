"""Synthetic daemon boundaries, no live Docker or Protect calls."""
import json
from pathlib import Path

import pytest

from aikey.aiport_nas_reconcile import ReconcileError, verify_inputs
from aikey.nas_reconcile_intent import execute, read
from aikey import host_activation
from test_aiport_nas_reconcile import inputs, FakeDocker


class Docker(FakeDocker):
    def __init__(self, manifest, *, lose_ack=False, fail_stop=False, **kwargs):
        super().__init__(manifest, **kwargs)
        self.lose_ack = lose_ack
        self.fail_stop = fail_stop
        self.operation = None
        self.created = False

    def __call__(self, argv, timeout):
        if '-f' in argv:
            recipe = json.loads(Path(argv[argv.index('-f')+1]).read_text())
            self.operation = recipe['services']['aiport_slot_2']['labels']['org.aikey.operation']
        if 'inspect' in argv and '.Config.Labels' in argv[3]:
            return json.dumps({'org.aikey.operation': self.operation})
        if 'start' in argv:
            self.created = True
            result = super().__call__(argv, timeout)
            if self.lose_ack:
                raise ReconcileError('lost ACK')
            return result
        if 'stop' in argv and self.fail_stop:
            self.calls.append(argv)
            raise ReconcileError('stop failed')
        if 'ps' in argv and self.created and not self.started:
            self.calls.append(argv)
            return json.dumps({'Service': 'aiport_slot_2', 'State': 'exited',
                               'ID': 'a'*12, 'Publishers': []})
        return super().__call__(argv, timeout)


def setup(tmp_path, **kwargs):
    plan, states, options, manifest, report = inputs(tmp_path)
    directory = tmp_path/'authority'
    directory.mkdir(mode=0o700)
    docker = Docker(manifest, **kwargs)
    arguments = dict(host_state=directory,
        verify=lambda: verify_inputs(plan, manifest, report, states, options),
        run=docker, pause=lambda _: None)
    return manifest, states, directory, docker, arguments


def test_complete_start_and_repeat_are_idempotent(tmp_path):
    manifest, states, directory, docker, args = setup(tmp_path)
    result = execute(tmp_path/'unused.json', manifest, states, **args)
    assert result['started'] == ['aiport_slot_2']
    assert read(directory)['phase'] == 'completed'
    assert host_activation.read(states[2]/'config.json')['phase'] == 'settled'
    assert execute(tmp_path/'unused.json', manifest, states, **args)['started'] == []
    assert sum('start' in call for call in docker.calls) == 1


def test_lost_ack_fences_retry_and_configuration(tmp_path):
    manifest, states, directory, docker, args = setup(tmp_path, lose_ack=True)
    with pytest.raises(ReconcileError, match='lost ACK'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert read(directory)['phase'] == 'uncertain'
    assert docker.started
    assert not any('stop' in call for call in docker.calls)
    with pytest.raises(ReconcileError, match='pending_reconciliation'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    with pytest.raises(ValueError, match='original_host_reconciliation'):
        host_activation.check_writable(states[2]/'config.json')
    with pytest.raises(ReconcileError, match='terminal_effect'):
        execute(tmp_path/'unused.json', manifest, states, settle=True, **args)


@pytest.mark.parametrize('fail_stop', [False, True])
def test_failed_health_stops_only_observed_id_and_records_uncertainty(tmp_path, fail_stop):
    manifest, states, directory, docker, args = setup(tmp_path, fail_health=True, fail_stop=fail_stop)
    with pytest.raises(ReconcileError, match='health check'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    stops = [call for call in docker.calls if 'stop' in call]
    assert stops == [['docker', 'stop', '--time', '10', 'a'*12]]
    assert read(directory)['phase'] == ('uncertain' if fail_stop else 'rolled_back')
    assert host_activation.read(states[2]/'config.json')['phase'] == ('prepared' if fail_stop else 'settled')


def test_stale_inventory_prevents_any_daemon_command(tmp_path):
    manifest, states, directory, docker, args = setup(tmp_path)
    def refuse():
        raise ReconcileError('stale')
    args['verify'] = refuse
    with pytest.raises(ReconcileError, match='stale'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert docker.calls == []
    assert read(directory) is None


def test_public_apply_entry_refuses_missing_authority(tmp_path):
    from aikey.aiport_nas_reconcile import reconcile
    manifest, states, _, docker, _ = setup(tmp_path)
    with pytest.raises(ReconcileError, match='host-state'):
        reconcile(tmp_path/'manifest.json', manifest, states, apply=True, run=docker)
    assert docker.calls == []


def test_container_replacement_is_never_stopped(tmp_path):
    manifest, states, directory, docker, args = setup(tmp_path, fail_health=True)
    original = docker.__call__
    health_failed = False
    def replaced(argv, timeout):
        nonlocal health_failed
        if 'exec' in argv:
            health_failed = True
        result = original(argv, timeout)
        if 'ps' in argv and health_failed:
            result = result.replace('a'*12, 'b'*12)
        return result
    args['run'] = replaced
    with pytest.raises(ReconcileError):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert not any('stop' in call for call in docker.calls)
    assert read(directory)['phase'] == 'uncertain'


def test_recipe_tamper_refused_before_daemon_mutation(tmp_path):
    manifest, states, directory, docker, args = setup(tmp_path)
    original = docker.__call__
    def tamper(argv, timeout):
        result = original(argv, timeout)
        if 'config' in argv:
            Path(argv[argv.index('-f')+1]).write_text('{}')
        return result
    args['run'] = tamper
    with pytest.raises(ReconcileError, match='recipe_changed'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert not any('start' in call for call in docker.calls)


def test_guardian_docker_policy_binds_commands_and_effect_slots():
    from aikey.host_runtime_command import allowed, checked_effect
    from aikey.nas_reconcile_intent import digest
    assert allowed(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', 'stop', '--time', '10', 'a'*64])
    assert not allowed(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', 'stop', '--time', '10', 'unrelated-service'])
    assert not allowed(['/usr/bin/docker', 'start', 'a'*64])
    assert not allowed(['/usr/bin/docker', 'rm', '-f', 'a'*64])
    assert not allowed(['/usr/bin/docker', 'compose', '-f', '/tmp/arbitrary', 'up'])
    assert checked_effect({'operation': 'b'*32, 'action': 'nas_create_16',
        'authority': digest(b'fixture'), 'authority_device': 1, 'authority_inode': 2,
        'argv_sha256': digest(b'argv')})

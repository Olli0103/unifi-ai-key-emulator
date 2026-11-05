"""Docker clears live endpoint fields on stop; never guess an unknown binding."""
import json

import pytest

from aikey.aiport_nas_reconcile import ReconcileError
from aikey.nas_reconcile_intent import execute, read
from test_nas_reconcile_intent import Docker, setup


class EndpointDocker(Docker):
    def __init__(self, manifest):
        super().__init__(manifest, adopted=True, control_connected=True)
        self.ever_started = False
        self.fault = None

    def __call__(self, argv, timeout):
        if 'start' in argv:
            self.ever_started = True
        value = super().__call__(argv, timeout)
        if 'ps' in argv and value:
            row = json.loads(value)
            if self.created and not self.started:
                row['State'] = 'exited' if self.ever_started else 'created'
            if self.fault == 'replacement':
                row['ID'] = 'b' * 12
            return json.dumps(row)
        if 'inspect' in argv and '.NetworkSettings.Networks' in argv[3]:
            networks = json.loads(value)
            for endpoint in networks.values():
                endpoint['IPAMConfig'] = {'IPv4Address': endpoint['IPAddress']}
                if not self.started:
                    endpoint['IPAddress'] = ''
                    if self.ever_started:
                        endpoint['MacAddress'] = ''
                if self.fault == 'saved_ip':
                    endpoint['IPAMConfig']['IPv4Address'] = '192.168.10.220'
                if self.fault == 'missing_ipam':
                    endpoint['IPAMConfig'] = None
                if self.fault == 'wrong_mac' or self.fault == 'running_mac' and self.started:
                    endpoint['MacAddress'] = '02:00:00:00:00:ff'
                if self.fault == 'created_missing_mac':
                    endpoint['MacAddress'] = ''
            return json.dumps(networks)
        return value


def fixture(tmp_path, *, running=False):
    manifest, states, directory, _, args = setup(tmp_path)
    docker = EndpointDocker(manifest)
    if running:
        docker.started = docker.created = docker.ever_started = True
    args['run'] = docker
    return manifest, states, directory, docker, args


def test_new_created_endpoint_uses_explicit_ipam_and_requested_mac(tmp_path):
    manifest, states, directory, docker, args = fixture(tmp_path)
    result = execute(tmp_path/'unused.json', manifest, states, **args)
    assert result['started'] == ['aiport_slot_2']
    assert read(directory)['phase'] == 'completed'
    assert sum('create' in c for c in docker.calls) == 1
    assert sum('start' in c for c in docker.calls) == 1


def test_verified_noop_persists_binding_for_same_container_restart(tmp_path):
    manifest, states, directory, docker, args = fixture(tmp_path, running=True)
    assert execute(tmp_path/'unused.json', manifest, states, **args)['started'] == []
    previous = read(directory)
    assert previous['phase'] == 'completed'
    assert previous['before']['aiport_slot_2']['NetworkVerified'] is True
    docker.started = False
    assert execute(tmp_path/'unused.json', manifest, states, **args)['started'] == ['aiport_slot_2']
    assert read(directory)['operation'] != previous['operation']
    assert sum('start' in c for c in docker.calls) == 1
    assert not any('create' in c for c in docker.calls)


def test_completed_creation_can_restart_same_stopped_id(tmp_path):
    manifest, states, directory, docker, args = fixture(tmp_path)
    execute(tmp_path/'unused.json', manifest, states, **args)
    docker.started = False
    assert execute(tmp_path/'unused.json', manifest, states, **args)['started'] == ['aiport_slot_2']
    assert sum('create' in c for c in docker.calls) == 1


@pytest.mark.parametrize('fault', ['saved_ip', 'missing_ipam', 'wrong_mac', 'replacement',
                                  'configuration_changed', 'no_prior_receipt'])
def test_stopped_unknown_or_changed_binding_refuses_before_start(tmp_path, fault):
    manifest, states, directory, docker, args = fixture(tmp_path, running=True)
    if fault != 'no_prior_receipt':
        execute(tmp_path/'unused.json', manifest, states, **args)
    docker.calls.clear()
    docker.started = False
    docker.fault = fault
    if fault == 'configuration_changed':
        path = states[2]/'config.json'
        value = json.loads(path.read_text())
        value['firmware_version'] = '5.1.13'
        path.write_text(json.dumps(value))
    with pytest.raises(ReconcileError, match='network, IP or MAC'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert not any('start' in c or 'create' in c for c in docker.calls)


def test_live_mac_must_match_again_after_a_verified_restart(tmp_path):
    manifest, states, directory, docker, args = fixture(tmp_path, running=True)
    execute(tmp_path/'unused.json', manifest, states, **args)
    docker.started = False
    docker.fault = 'running_mac'
    with pytest.raises(ReconcileError, match='network, IP or MAC'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert read(directory)['phase'] == 'uncertain'
    assert read(directory)['actions'][-1]['state'] == 'requested'
    with pytest.raises(ReconcileError, match='pending_reconciliation'):
        execute(tmp_path/'unused.json', manifest, states, **args)


def test_created_endpoint_without_mac_is_not_a_verified_stopped_container(tmp_path):
    manifest, states, directory, docker, args = fixture(tmp_path)
    docker.fault = 'created_missing_mac'
    with pytest.raises(ReconcileError, match='network, IP or MAC'):
        execute(tmp_path/'unused.json', manifest, states, **args)
    assert not any('start' in c for c in docker.calls)


@pytest.mark.parametrize('health_ip', ['172.27.1.22', '172.27.1.23', None])
def test_stopped_health_network_requires_its_own_exact_saved_address(tmp_path, health_ip):
    from aikey.aiport_nas_reconcile import _inspect
    manifest, _, _, docker, _ = fixture(tmp_path, running=True)
    docker.started = False
    service = manifest['services']['aiport_slot_2']
    service['networks']['aiport_health'] = {'ipv4_address': '172.27.1.22'}
    lan = manifest['networks']['aiport_lan'].get('name') or manifest['name']+'_aiport_lan'
    def run(argv, timeout):
        value = docker(argv, timeout)
        if 'inspect' in argv and '.NetworkSettings.Networks' in argv[3]:
            networks = json.loads(value)
            networks['fictional_health'] = {'IPAddress': '', 'IPAMConfig': {'IPv4Address': health_ip}}
            return json.dumps(networks)
        return value
    row = {'Service': 'aiport_slot_2', 'State': 'exited', 'ID': 'a'*12}
    if health_ip == '172.27.1.22':
        _inspect(run, row, service, lan, 'fictional_health', verified_stopped_id='a'*12)
    else:
        with pytest.raises(ReconcileError, match='health address'):
            _inspect(run, row, service, lan, 'fictional_health', verified_stopped_id='a'*12)

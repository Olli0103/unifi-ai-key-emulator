"""Full ordinary actor with a fictional, separately privileged PF adapter."""

import pytest

from aikey.aiport_apple_plan import build_manifest, create_arguments
from aikey.aiport_apple_reconcile import AppleOutcomeUncertain, AppleRuntimeError, read_receipt, settle
from aikey.aiport_apple_transport import PFTransport
from test_aiport_apple_reconcile import Host, actor_fixture, invoke, entry


class PFHost(Host):
    def run(self, argv, timeout):
        result = super().run(argv, timeout)
        if argv[1] == 'create':
            for row in self.rows.values():
                row['configuration']['publishedPorts'] = []
        return result


class Privileged:
    def __init__(self):
        self.calls = []
        self.active = False
        self.fail = None
        self.operation = None

    def request(self, value):
        self.calls.append(dict(value))
        action = value['action']
        if action == 'apply':
            self.active = True
            self.operation = value['operation']
        if action in ('verify', 'settle'):
            assert self.active
            assert value['operation'] in ('0' * 32, self.operation)
        if action == 'remove':
            assert value['operation'] == self.operation
            self.active = False
        if action == self.fail:
            raise AppleRuntimeError('fictional_pf_lost_acknowledgement')
        return dict(value, verified=True, **({'target': '192.168.64.3'} if action != 'preflight' else {}))


def setup(tmp_path):
    inputs = list(actor_fixture(tmp_path))
    inputs[4]['transport'] = 'pf-rdr'
    inputs[0] = build_manifest(inputs[1], inputs[2], inputs[3], **inputs[4])
    host = PFHost(inputs[0])
    privileged = Privileged()
    adapter = PFTransport(inputs[4]['controller_ip'], request=privileged.request)
    host.check = adapter
    return inputs, host, privileged, adapter


def test_explicit_manifest_changes_revision_and_never_publishes_tcp_proxy(tmp_path):
    inputs, host, privileged, adapter = setup(tmp_path)
    row = inputs[0]['instances'][0]
    args = create_arguments(inputs[0], row, 'a' * 32)
    assert '--publish' not in args
    default = build_manifest(inputs[1], inputs[2], inputs[3], **dict(inputs[4], transport='apple-publish'))
    assert default['revision'] != inputs[0]['revision'] and 'transport' not in default
    with pytest.raises(AppleRuntimeError, match='adapter_required'):
        invoke(inputs, host, apply=True)
    assert not host.mutations()
    result = invoke(inputs, host, apply=True, transport=adapter)
    assert result['mode'] == 'applied'
    receipt = read_receipt(inputs[-1])
    assert [r['action'] for r in receipt['actions']] == ['create', 'start', 'transport_apply']
    assert all(r['state'] == 'observed' for r in receipt['actions'])
    assert privileged.active
    assert host.rows[row['name']]['configuration']['publishedPorts'] == []


def test_health_failure_removes_only_own_mapping_then_stops_new_container(tmp_path):
    inputs, host, privileged, adapter = setup(tmp_path)
    host.ready = False
    with pytest.raises(AppleRuntimeError, match='rolled_back'):
        invoke(inputs, host, apply=True, transport=adapter)
    receipt = read_receipt(inputs[-1])
    assert receipt['phase'] == 'rolled_back'
    assert [r['action'] for r in receipt['actions']] == [
        'create', 'start', 'transport_apply', 'transport_remove', 'stop']
    assert not privileged.active
    assert next(iter(host.rows.values()))['status']['state'] == 'stopped'


def test_lost_transport_ack_fences_retry_and_positive_settlement_does_not_repeat_apply(tmp_path):
    inputs, host, privileged, adapter = setup(tmp_path)
    privileged.fail = 'apply'
    with pytest.raises(AppleOutcomeUncertain):
        invoke(inputs, host, apply=True, transport=adapter)
    assert read_receipt(inputs[-1])['phase'] == 'uncertain'
    count = len(host.mutations())
    with pytest.raises(AppleOutcomeUncertain, match='pending'):
        invoke(inputs, host, apply=True, transport=adapter)
    assert len(host.mutations()) == count and privileged.active
    privileged.fail = None
    result = settle(*inputs, run=host.run, probe=host.probe, transport=adapter)
    assert result['outcome'] == 'all_running' and result['daemon_mutations'] == 0
    assert len(host.mutations()) == count
    assert [v['action'] for v in privileged.calls].count('apply') == 1
    assert [v['action'] for v in privileged.calls].count('settle') == 1
    assert read_receipt(inputs[-1])['failure'] == 'recovery_incomplete'


def test_existing_published_instance_is_not_silently_recreated(tmp_path):
    inputs, host, privileged, adapter = setup(tmp_path)
    row = inputs[0]['instances'][0]
    host.rows[row['name']] = entry(row, inputs[0])
    with pytest.raises(AppleRuntimeError, match='configuration_drift'):
        invoke(inputs, host, apply=True, transport=adapter)
    assert not host.mutations() and not privileged.calls


@pytest.mark.parametrize('response', [{}, {'verified': True}, {'verified': False},
    {'target': '127.0.0.1'}, {'operation': 'c' * 32}, {'extra': 'field'}])
def test_client_rejects_incomplete_unbound_or_injected_receipts(response):
    def request(value):
        return dict(value, verified=True, target='192.168.64.3', **{}) | response
    adapter = PFTransport('192.168.10.1', request=request)
    # The first three variants exercise explicit malformed result shapes.
    if response in ({}, {'verified': True}):
        adapter.request = lambda _: response
    with pytest.raises(ValueError):
        adapter.verify({'name': 'fixture', 'host_ip': '192.168.10.135', 'certificate_sha256': 'a' * 64})


def test_client_requires_root_owned_install_without_prompt_or_daemon_change(monkeypatch, tmp_path):
    import aikey.aiport_apple_transport as module
    helper = tmp_path / 'untrusted.py'
    helper.write_text('raise SystemExit(0)')
    monkeypatch.setattr(module, 'HELPER', helper)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('must not execute'))
    with pytest.raises(AppleRuntimeError, match='unavailable'):
        PFTransport('192.168.10.1').verify({'name': 'fixture', 'host_ip': '192.168.10.135', 'certificate_sha256': 'a' * 64})

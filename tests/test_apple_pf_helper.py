"""Fictional PF/daemon responses; no root, sockets or host changes."""
from copy import deepcopy
import json
import os

import pytest

from aikey.apple_pf_helper import Adapter, Refused, anchor, policy


def fixture():
    return {'uid': os.geteuid() or 501, 'container': '/usr/bin/true',
            'guest_network': '192.168.64.0/24', 'slots': [{
                'name': 'fixture-port-1', 'host_ip': '192.168.10.135',
                'controller_ip': '192.168.10.1', 'interface': 'en0',
                'certificate_sha256': 'a' * 64}]}


class Host:
    def __init__(self):
        self.value = fixture()
        self.rows = {}
        self.rules = ''
        self.calls = []
        self.target = '192.168.64.3/24'
        self.enabled = True
        self.route = 'en0'
        self.main = 'rdr-anchor "com.apple/*" all\n'
        self.unknown = False
        self.fail = None
        self.binds = []
        self.peers = []
        self.config = {'id': 'fixture-port-1', 'publishedPorts': [],
                       'labels': {'org.aikey.instance': 'a' * 64}}

    def run(self, args, data=None):
        self.calls.append((args, data))
        if args == ['/sbin/ifconfig']:
            return 'en0: flags=active\n inet 192.168.10.135 netmask 0xffffff00\n'
        if args[:3] == ['/sbin/route', '-n', 'get']:
            return 'interface: ' + (self.route if args[-1] == '192.168.10.1' else 'bridge100') + '\n'
        if args[0] == '/usr/sbin/sysctl':
            return '1\n'
        if args[0].endswith('socketfilterfw'):
            return 'Firewall is disabled. (State = 0)\n'
        if args[0] == '/bin/launchctl':
            assert args[1:3] == ['asuser', str(self.value['uid'])]
            assert args[3:6] == ['/usr/bin/sudo', '-H', '-u']
            return json.dumps([{'configuration': self.config,
                'status': {'state': 'running', 'networks': [{'network': 'default', 'ipv4Address': self.target}]}}])
        assert args[0] == '/sbin/pfctl'
        if args[1:] == ['-si']:
            return 'Status: Enabled\n' if self.enabled else 'Status: Disabled\n'
        if args[1:] == ['-sn']:
            return self.main
        if args[1:] == ['-sr']:
            return 'scrub-anchor "com.apple/*" all\n'
        assert anchor(self.value['slots'][0]) in args
        if args[-1] in ('-sr', 'Anchors'):
            return ''
        if args[-1] == '-sn':
            return self.rules.replace(' port 443 ', ' port = 443 ')
        if '-n' in args:
            return ''
        if '-F' in args:
            assert args[-1] == 'nat'
            self.rules = ''
        else:
            assert args[-2:] == ['-f', '-']
            self.rules = data
        if self.fail == 'ack':
            raise OSError('fictional lost acknowledgement')
        return ''

    def save(self, name, value):
        self.rows[name] = deepcopy(value)

    def adapter(self):
        return Adapter(self.value, run=self.run, read_state=self.rows.get,
                       save_state=self.save, bind_check=self.binds.append, peer_check=lambda *value: self.peers.append(value))

    def request(self, action, operation='b' * 32):
        row = self.value['slots'][0]
        return dict({key: row[key] for key in ('name', 'host_ip', 'controller_ip', 'certificate_sha256')},
                    action=action, operation=operation)

    def invoke(self, action, operation='b' * 32):
        return self.adapter().handle(self.request(action, operation))

    def mutations(self):
        return [c for c in self.calls if '-F' in c[0] or '-f' in c[0] and '-n' not in c[0]]


def test_preflight_readonly_then_apply_verify_restart_refresh_and_owned_remove():
    host = Host()
    host.invoke('preflight')
    assert not host.rows and not host.mutations()
    host.invoke('apply')
    assert host.rules == ('rdr pass on en0 inet proto tcp from 192.168.10.1 '
                          'to 192.168.10.135 port 443 -> 192.168.64.3 port 8443\n')
    count = len(host.mutations())
    assert host.invoke('verify')['target'] == '192.168.64.3'
    assert len(host.mutations()) == count
    host.target = '192.168.64.4/24'
    with pytest.raises(Refused):
        host.invoke('verify')
    host.invoke('apply', 'c' * 32)
    assert '192.168.64.4' in host.rules
    with pytest.raises(Refused):
        host.invoke('remove')
    host.invoke('remove', 'c' * 32)
    assert host.rules == '' and host.rows['fixture-port-1']['phase'] == 'removed'
    host.invoke('remove', 'c' * 32)
    assert len(host.mutations()) == 3
    assert all(c[0][:2] == ['/sbin/pfctl', '-a'] for c in host.mutations())


@pytest.mark.parametrize('fault', ['disabled', 'route', 'anchor', 'foreign_rules', 'guest',
    'gateway', 'owner', 'publish', 'name', 'operation', 'action', 'extra', 'state'])
def test_refusal_before_mutation(fault):
    host = Host()
    request = host.request('apply')
    if fault == 'disabled':
        host.enabled = False
    elif fault == 'route':
        host.route = 'utun0'
    elif fault == 'anchor':
        host.main = ''
    elif fault == 'foreign_rules':
        host.rules = 'rdr pass from any to any -> 192.168.64.9\n'
    elif fault == 'guest':
        host.target = '192.168.10.99/24'
    elif fault == 'gateway':
        host.target = '192.168.64.1/24'
    elif fault == 'owner':
        host.config['labels']['org.aikey.instance'] = 'd' * 64
    elif fault == 'publish':
        host.config['publishedPorts'] = [{'hostPort': 443}]
    elif fault in ('name', 'operation', 'action'):
        request[fault] = 'untrusted;input'
    elif fault == 'extra':
        request['rule'] = 'rdr pass from any to any'
    else:
        host.rows['fixture-port-1'] = {'phase': 'active'}
    with pytest.raises((Refused, ValueError, KeyError)):
        host.adapter().handle(request)
    assert not host.mutations()


def test_uncertain_apply_is_fenced_but_can_be_positively_verified_and_settled():
    host = Host()
    host.fail = 'ack'
    with pytest.raises(OSError):
        host.invoke('apply')
    assert host.rows['fixture-port-1']['phase'] == 'prepared'
    host.fail = None
    with pytest.raises(Refused):
        host.invoke('apply')
    host.invoke('verify')
    assert host.rows['fixture-port-1']['phase'] == 'prepared'
    with pytest.raises(Refused):
        host.invoke('settle', 'c' * 32)
    host.invoke('settle')
    assert host.rows['fixture-port-1']['phase'] == 'active'
    assert len(host.mutations()) == 1


@pytest.mark.parametrize('field,value', [('name', '../escape'), ('host_ip', '0.0.0.0'),
    ('host_ip', '192.168.64.3'), ('controller_ip', '127.0.0.1'),
    ('interface', 'en0\nrdr pass'), ('certificate_sha256', 'no')])
def test_policy_rejects_unbounded_authority(field, value):
    configured = fixture()
    configured['slots'][0][field] = value
    with pytest.raises((Refused, ValueError)):
        policy(configured)


def test_actual_loopback_tls_leaf_pin_accepts_exact_and_rejects_wrong_peer(tmp_path, monkeypatch):
    import hashlib
    import socket
    import ssl
    import threading
    import aikey.apple_pf_helper as module
    from test_aiport_apple_plan import fixture as profiles

    _, _, states, _ = profiles(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(states[1] / 'device.crt', states[1] / 'device.key')
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert((states[1] / 'device.crt').read_text())).hexdigest()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(2)
    listener.settimeout(5)
    address = listener.getsockname()
    errors = []
    def serve():
        try:
            for _ in range(2):
                connection, _ = listener.accept()
                with context.wrap_socket(connection, server_side=True) as tls:
                    tls.settimeout(2)
                    tls.recv(1)
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            listener.close()
    worker = threading.Thread(target=serve)
    worker.start()
    connect = socket.create_connection
    def local(target, timeout):
        assert target == ('192.168.64.3', 8443)
        return connect(address, timeout=timeout)
    monkeypatch.setattr(module.socket, 'create_connection', local)
    try:
        module.pinned_peer('192.168.64.3', pin)
        with pytest.raises(Refused):
            module.pinned_peer('192.168.64.3', 'f' * 64)
    finally:
        worker.join(6)
    assert not worker.is_alive() and not errors


def test_wrong_guest_certificate_never_loads_a_rule_or_creates_a_journal():
    host = Host()
    adapter = host.adapter()
    adapter.peer_check = lambda *_: (_ for _ in ()).throw(Refused('wrong_actual_peer'))
    with pytest.raises(Refused, match='wrong_actual_peer'):
        adapter.handle(host.request('apply'))
    assert not host.mutations() and not host.rows


@pytest.mark.parametrize('field', ['host_ip', 'controller_ip', 'certificate_sha256'])
def test_request_must_match_exact_privileged_policy_not_only_name(field):
    host = Host()
    request = host.request('apply')
    request[field] = 'd' * 64 if field == 'certificate_sha256' else '192.168.10.200'
    with pytest.raises(Refused):
        host.adapter().handle(request)
    assert not host.calls and not host.rows

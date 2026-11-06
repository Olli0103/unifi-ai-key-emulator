"""Separately installed, root-owned PF adapter; standard library only.

Not an installer. Never run this from a writable checkout as a privileged
service. A root-owned policy explicitly authorizes names, LAN endpoints and
certificate labels. No aliases, sysctl changes, main rules or PF enable/disable.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import socket
import signal
import ssl
import subprocess
import sys
import time

POLICY = Path('/Library/Application Support/LocalAIPortPF/policy.json')
STATE = Path('/Library/Application Support/LocalAIPortPF/state')
NAME = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')


class Refused(ValueError):
    pass


def require(condition):
    if not condition:
        raise Refused('apple_pf_contract_refused')


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(raw):
    require(len(raw) <= 2 * 1024 * 1024)
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False))


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def private_ip(value):
    require(isinstance(value, str))
    address = ipaddress.IPv4Address(value)
    require(address.is_private and not (address.is_loopback or address.is_multicast
                                       or address.is_unspecified or address.is_link_local))
    require(str(address) == value)
    return value


def protected(path, *, directory=False):
    require(path.is_absolute() and path.resolve() == path)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode))
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    if not directory:
        require(info.st_nlink == 1)
    return path


def read(path):
    protected(path)
    with path.open('rb') as source:
        raw = source.read(2 * 1024 * 1024 + 1)
    return decode(raw)


def policy(value):
    require(isinstance(value, dict) and set(value) == {'uid', 'container', 'guest_network', 'slots'})
    require(type(value['uid']) is int and value['uid'] > 0)
    path = Path(value['container'])
    require(path.is_absolute() and path.resolve() == path and path.is_file())
    # This executable is invoked ONLY after switching to its ordinary owner.
    require(not any(c in str(path) for c in '\r\n\x00'))
    network = ipaddress.IPv4Network(value['guest_network'], strict=True)
    require(network.is_private and not network.is_loopback and 16 <= network.prefixlen <= 30)
    slots = value['slots']
    require(isinstance(slots, list) and 1 <= len(slots) <= 16)
    names, addresses, certs = set(), set(), set()
    for row in slots:
        require(isinstance(row, dict) and set(row) == {'name', 'host_ip', 'controller_ip',
                'interface', 'certificate_sha256'})
        require(isinstance(row['name'], str) and NAME.fullmatch(row['name']))
        require(isinstance(row['interface'], str) and re.fullmatch(r'en[0-9]{1,3}', row['interface']))
        require(isinstance(row['certificate_sha256'], str) and HASH.fullmatch(row['certificate_sha256']))
        private_ip(row['host_ip'])
        private_ip(row['controller_ip'])
        require(row['host_ip'] != row['controller_ip'])
        require(ipaddress.ip_address(row['host_ip']) not in network
                and ipaddress.ip_address(row['controller_ip']) not in network)
        require(row['name'] not in names and row['host_ip'] not in addresses
                and row['certificate_sha256'] not in certs)
        names.add(row['name'])
        addresses.add(row['host_ip'])
        certs.add(row['certificate_sha256'])
    return value


def command(argv, data=None):
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/root', 'LANG': 'C'}
    result = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=15, env=env)
    require(result.returncode == 0 and len(result.stdout.encode()) <= 2 * 1024 * 1024)
    return result.stdout


def anchor(row):
    return 'com.apple/local-aiport-' + hashlib.sha256(row['name'].encode()).hexdigest()[:24]


def rule(row, target):
    private_ip(target)
    return (f"rdr pass on {row['interface']} inet proto tcp from {row['controller_ip']} "
            f"to {row['host_ip']} port 443 -> {target} port 8443\n")


def normalized(value):
    # pfctl can print service names and default /32 masks on numeric addresses.
    return ' '.join(value.replace(' port = ', ' port ').replace(' port https ', ' port 443 ').replace('/32', '').split())


class Adapter:
    def __init__(self, value, *, run=command, read_state=None, save_state=None, bind_check=None, peer_check=None):
        self.policy = policy(value)
        self.run = run
        self.read_state = read_state
        self.save_state = save_state
        self.bind_check = bind_check or available
        self.peer_check = peer_check or pinned_peer

    def preflight(self, row):
        self.bind_check(row['host_ip'])
        text = self.run(['/sbin/ifconfig'])
        interfaces = []
        current = None
        for line in text.splitlines():
            header = re.match(r'^([A-Za-z0-9_.-]+):', line)
            if header:
                current = header[1]
            if re.match(r'\s+inet ' + re.escape(row['host_ip']) + r'\s', line):
                interfaces.append(current)
        require(interfaces == [row['interface']])
        route = self.run(['/sbin/route', '-n', 'get', row['controller_ip']])
        require(re.findall(r'^\s*interface:\s*(\S+)\s*$', route, re.M) == [row['interface']])
        require(self.run(['/usr/sbin/sysctl', '-n', 'net.inet.ip.forwarding']).strip() == '1')
        require(re.search(r'^Status: Enabled\b', self.run(['/sbin/pfctl', '-si']), re.M))
        main = self.run(['/sbin/pfctl', '-sn'])
        require('rdr-anchor "com.apple/*"' in main)
        require(self.run(['/usr/libexec/ApplicationFirewall/socketfilterfw', '--getglobalstate']).strip()
                == 'Firewall is disabled. (State = 0)')
        return main, self.run(['/sbin/pfctl', '-sr'])

    def target(self, row):
        uid = self.policy['uid']
        user = pwd.getpwuid(uid).pw_name
        argv = ['/bin/launchctl', 'asuser', str(uid), '/usr/bin/sudo', '-H', '-u', user,
                self.policy['container'], 'inspect', row['name']]
        values = decode(self.run(argv))
        require(isinstance(values, list) and len(values) == 1)
        value = values[0]
        config = value['configuration']
        require(config['id'] == row['name'] and value.get('id', row['name']) == row['name'])
        require(config.get('publishedPorts') == [] and config.get('publishedSockets', []) == [])
        require(config['labels']['org.aikey.instance'] == row['certificate_sha256'])
        require(value['status']['state'] == 'running')
        networks = value['status']['networks']
        require(isinstance(networks, list) and len(networks) == 1 and networks[0]['network'] == 'default')
        target = ipaddress.IPv4Interface(networks[0]['ipv4Address']).ip
        network = ipaddress.IPv4Network(self.policy['guest_network'])
        require(target in network and target not in (network.network_address, network.broadcast_address,
                                                       network.network_address + 1))
        route = self.run(['/sbin/route', '-n', 'get', str(target)])
        require(re.findall(r'^\s*interface:\s*(\S+)\s*$', route, re.M) == ['bridge100'])
        return str(target)

    def handle(self, request):
        require(isinstance(request, dict) and set(request) == {'action', 'name', 'operation',
                'host_ip', 'controller_ip', 'certificate_sha256'})
        action, name, operation = request['action'], request['name'], request['operation']
        require(action in ('preflight', 'apply', 'verify', 'settle', 'remove'))
        require(isinstance(operation, str) and TOKEN.fullmatch(operation))
        rows = [r for r in self.policy['slots'] if r['name'] == name]
        require(len(rows) == 1)
        row = rows[0]
        require(all(request[key] == row[key] for key in ('host_ip', 'controller_ip', 'certificate_sha256')))
        require(action in ('preflight', 'verify') or operation != '0' * 32)
        main_before = self.preflight(row)
        owned = anchor(row)
        current = self.run(['/sbin/pfctl', '-a', owned, '-sn'])
        require(not self.run(['/sbin/pfctl', '-a', owned, '-sr']).strip())
        require(not self.run(['/sbin/pfctl', '-a', owned, '-s', 'Anchors']).strip())
        previous = self.read_state(name)
        if previous is not None:
            require(isinstance(previous, dict) and set(previous) == {'row', 'operation', 'target', 'phase'})
            require(previous['row'] == row and previous['phase'] in ('prepared', 'active', 'removed'))
            require(isinstance(previous['operation'], str) and TOKEN.fullmatch(previous['operation']))
            private_ip(previous['target'])
            require(normalized(current) == ('' if previous['phase'] == 'removed'
                                            else normalized(rule(row, previous['target']))))
        else:
            require(not current.strip())
        if action == 'preflight':
            require(previous is None or previous['phase'] != 'prepared')
            return dict(request, verified=True)
        if action == 'remove':
            require(previous is not None and previous['operation'] == operation)
            if previous['phase'] != 'removed':
                self.save_state(name, dict(previous, phase='prepared'))
                self.run(['/sbin/pfctl', '-a', owned, '-F', 'nat'])
                require(not self.run(['/sbin/pfctl', '-a', owned, '-sn']).strip())
                self.save_state(name, dict(previous, phase='removed'))
            target = previous['target']
        else:
            target = self.target(row)
            self.peer_check(target, row['certificate_sha256'])
            expected = rule(row, target)
            if action in ('verify', 'settle'):
                require(previous is not None and previous['phase'] in ('active', 'prepared'))
                require(previous['target'] == target and normalized(current) == normalized(expected))
                require(operation == '0' * 32 or previous['operation'] == operation)
                if action == 'settle':
                    require(operation != '0' * 32 and previous['operation'] == operation)
                    self.save_state(name, dict(previous, phase='active'))
                # Verify is read-only, including recovery of a lost acknowledgement.
            else:
                require(previous is None or previous['phase'] != 'prepared')
                if previous is not None and previous['phase'] == 'active':
                    # Refresh only the same authorized slot after its actual restart.
                    require(previous['row'] == row)
                self.run(['/sbin/pfctl', '-n', '-a', owned, '-f', '-'], expected)
                record = {'row': row, 'operation': operation, 'target': target, 'phase': 'prepared'}
                self.save_state(name, record)
                self.run(['/sbin/pfctl', '-a', owned, '-f', '-'], expected)
                require(normalized(self.run(['/sbin/pfctl', '-a', owned, '-sn'])) == normalized(expected))
                require(self.target(row) == target)
                self.save_state(name, dict(record, phase='active'))
        require((self.run(['/sbin/pfctl', '-sn']), self.run(['/sbin/pfctl', '-sr'])) == main_before)
        return dict(request, verified=True, target=target)


@contextmanager
def storage(root):
    protected(root, directory=True)
    require(not root.stat().st_mode & 0o077)
    lock = os.open(root / '.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def path(name):
            require(isinstance(name, str) and NAME.fullmatch(name))
            return root / (name + '.json')
        def load(name):
            target = path(name)
            if not target.exists() and not target.is_symlink():
                return None
            return read(target)
        def save(name, value):
            target = path(name)
            temporary = root / (name + '.pending')
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(encode(value))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            require(load(name) == value)
        yield load, save
    finally:
        os.close(lock)


def pinned_peer(target, expected):
    # No HTTP or credentials: independently pin the actual guest DER leaf.
    # The ordinary health reader separately checks CA and the same leaf.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + 12
    while True:
        try:
            with socket.create_connection((target, 8443), timeout=2) as stream:
                with context.wrap_socket(stream, server_hostname=target) as tls:
                    certificate = tls.getpeercert(binary_form=True)
                    require(bool(certificate) and hashlib.sha256(certificate).hexdigest() == expected)
                    return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)



def available(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.settimeout(2)
        stream.bind((host, 443))


def expired(*_):
    raise Refused("apple_pf_deadline")


def main():
    try:
        require(os.geteuid() == 0 and platform.system() == 'Darwin')
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(80)
        value = policy(read(POLICY))
        require(os.environ.get('SUDO_UID') == str(value['uid']))
        request = decode(sys.stdin.buffer.read(8193))
        require(len(encode(request)) <= 8192)
        with storage(STATE) as (load, save):
            result = Adapter(value, read_state=load, save_state=save).handle(request)
        print(encode(result).decode(), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print('{"error":"apple_pf_refused_or_uncertain"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Unprivileged client for the separately authorized, root-owned PF adapter."""
import json
import os
from pathlib import Path
import stat
import subprocess

HELPER = Path('/Library/PrivilegedHelperTools/local-aiport-pf.py')
ZERO = '0' * 32


class PFTransport:
    def __init__(self, controller_ip, *, request=None):
        self.controller_ip = controller_ip
        self.request = request or self._request
        self.observations = []

    @staticmethod
    def _request(value):
        from .aiport_apple_reconcile import AppleRuntimeError
        try:
            # The root policy and helper are installed by an administrator,
            # never generated/installed or made writable by this operator.
            for path in (*HELPER.parents, HELPER):
                info = path.lstat()
                if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                    raise ValueError
            if not stat.S_ISREG(HELPER.stat().st_mode):
                raise ValueError
            env = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME') if key in os.environ}
            env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
            result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-I', str(HELPER)],
                input=json.dumps(value), text=True, capture_output=True, timeout=90, env=env)
            if result.returncode or len(result.stdout) > 8192:
                raise ValueError
            from .protocol import _json_object
            return _json_object(result.stdout.encode())
        except (OSError, ValueError, subprocess.SubprocessError):
            raise AppleRuntimeError('apple_pf_helper_unavailable_or_outcome_uncertain') from None

    def call(self, action, instance, operation=ZERO):
        from .aiport_apple_reconcile import AppleRuntimeError
        from .apple_pf_helper import private_ip
        value = {'action': action, 'name': instance['name'], 'operation': operation,
                 'host_ip': instance['host_ip'], 'controller_ip': self.controller_ip,
                 'certificate_sha256': instance['certificate_sha256']}
        result = self.request(value)
        fields = set(value) | {'verified'} | ({'target'} if action != 'preflight' else set())
        if (not isinstance(result, dict) or set(result) != fields or result.get('verified') is not True
                or any(result.get(key) != expected for key, expected in value.items())):
            raise AppleRuntimeError('apple_pf_readback_mismatch')
        if 'target' in result:
            private_ip(result['target'])
        return result

    def __call__(self, instance):
        self.call('preflight', instance)
        self.observations.append({'slot': instance['slot'], 'transport': 'pf-rdr',
                                  'root_policy_preflight': 'verified_no_changes'})
        return True

    def apply(self, instance, operation):
        return self.call('apply', instance, operation)

    def remove(self, instance, operation):
        return self.call('remove', instance, operation)

    def verify(self, instance, operation=ZERO):
        return self.call('verify', instance, operation)

    def settle(self, instance, operation):
        return self.call('settle', instance, operation)

    def probe(self, kind, instance):
        from .aiport_apple_reconcile import fresh_health
        if kind == 'aikey':
            return fresh_health(kind, instance)
        target = self.verify(instance)['target']
        # Host-originated traffic does not traverse the inbound LAN PF rule.
        # Use the same own-CA + leaf-pin health reader at the observed guest.
        return fresh_health(kind, instance, host_ip=target, port=8443)

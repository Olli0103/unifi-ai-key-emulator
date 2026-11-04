"""Actual owned TLS peers, not boolean substitutes for the CLI's health path."""
import asyncio
import hashlib
from types import SimpleNamespace

from aiohttp import web
import pytest

from aikey import apple_upgrade as upgrade, apple_supervisor as supervisor
from aikey.startup_configuration import observation
from test_admin_runtime_observation import target


@pytest.mark.parametrize('fault', ['none', 'different_leaf', 'stale_revision', 'changed_file', 'boolean_counter', 'redirect'])
async def test_upgrade_health_requires_own_tls_current_config_and_typed_counts(tmp_path, fault):
    directory = tmp_path / 'profile'
    directory.mkdir(mode=0o700)
    config = directory / 'config.json'
    config.write_bytes(b'{"synthetic":"configuration"}\n')
    config.chmod(0o600)
    value = {'service': 'local-aikey', 'device': {'adopted': True, 'connected': True},
             'search': {'connected': True}, 'worker': {'queued': 0, 'active': 0, 'pending': 0},
             'host_activation_schema': 'aikey-host-activation/1',
             'startup_configuration': observation(hashlib.sha256(config.read_bytes()).hexdigest())}
    if fault == 'stale_revision':
        value['startup_configuration'] = observation('f' * 64)
    if fault == 'boolean_counter':
        value['worker']['active'] = False
    response = (lambda: web.Response(status=302, headers={'Location': 'http://127.0.0.1:1/stolen'})) if fault == 'redirect' else value
    peer = directory
    if fault == 'different_leaf':
        from aikey.tls import ensure_identity_certificate
        ensure_identity_certificate(directory, '020000000077')
        peer = tmp_path / 'other'
    server, calls, url = await target(peer, response)
    try:
        port = server.port
        service = supervisor.Service('key', 'key-old', 'synthetic-image', '127.0.0.1', port, str(directory))
        args = SimpleNamespace(kind='aikey', slot_state=None, health_url=url, health_port=None)
        idle, ready = upgrade._probes(args, service)
        if fault == 'changed_file':
            config.write_bytes(b'{"synthetic":"changed after probe preparation"}')
        if fault == 'none':
            assert await asyncio.to_thread(idle) is True
            assert await asyncio.to_thread(ready) is True
            assert len(calls) == 2  # No cached readiness from the old process.
        else:
            with pytest.raises(Exception):
                await asyncio.to_thread(idle)
            if fault == 'different_leaf':
                assert calls == []
        assert not any(key.lower() in {'authorization', 'cookie'} for call in calls for key in call)
    finally:
        await server.close()


@pytest.mark.parametrize('field', ['health_url', 'health_port', 'slot_state'])
def test_endpoint_or_profile_mismatch_is_rejected_before_network(tmp_path, field):
    args = SimpleNamespace(kind='aikey', slot_state=None, health_url=None, health_port=None)
    setattr(args, field, tmp_path / 'other' if field == 'slot_state' else 123 if field == 'health_port' else 'https://127.0.0.2:443/healthz')
    service = supervisor.Service('key', 'old', 'synthetic', '127.0.0.1', 443, str(tmp_path))
    with pytest.raises(upgrade.UpgradeError, match='mismatch'):
        upgrade._probes(args, service)

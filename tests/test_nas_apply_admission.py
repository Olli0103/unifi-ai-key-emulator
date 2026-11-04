"""Regression: never expose a mutating entry without durable authority."""
import pytest
from aikey.aiport_nas_reconcile import ReconcileError, reconcile
from test_aiport_nas_reconcile import inputs, FakeDocker


def test_apply_requires_durable_authority_before_any_docker_call(tmp_path):
    _, states, _, manifest, _ = inputs(tmp_path)
    docker = FakeDocker(manifest)
    with pytest.raises(ReconcileError, match='host-state'):
        reconcile(tmp_path/'manifest.json', manifest, states, apply=True, run=docker)
    assert docker.calls == []


def test_dry_run_runner_uses_same_local_socket_and_no_ambient_docker_host(monkeypatch):
    from aikey.aiport_nas_reconcile import _run
    from types import SimpleNamespace
    import aikey.aiport_nas_reconcile as nas
    captured = []
    monkeypatch.setenv('DOCKER_HOST', 'tcp://unrelated.invalid:2375')
    def observe(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='[]', stderr='')
    monkeypatch.setattr(nas.subprocess, 'run', observe)
    assert _run(['docker', 'compose', '--project-name', 'fixture', '-f', '/fixture.json',
                 'ps', '--all', '--format', 'json']) == '[]'
    assert captured[0][0][:3] == ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
    assert 'DOCKER_HOST' not in captured[0][1]['env']

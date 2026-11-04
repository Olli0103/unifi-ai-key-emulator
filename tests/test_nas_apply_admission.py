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

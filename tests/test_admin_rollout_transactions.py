"""Maintenance writes include registration and share profile update locks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import json
from pathlib import Path
import threading

import pytest

from aikey import aiport_rollout as module
from aikey.aiport_config_store import AiPortConfigurationStore
from test_aiport_rollout import _one_new_nas_slot, _compose_settings, _locked_setup


@pytest.mark.parametrize("after_commit", [False, True])
def test_registration_failure_restores_compose_and_registry_with_identity_retained(tmp_path, monkeypatch, after_commit):
    root, compose, path, rollout, slots, report = _one_new_nas_slot(tmp_path)
    path.write_text(json.dumps(rollout))
    path.chmod(0o600)
    original = deepcopy(rollout)
    plan = module.plan_rollout(report, slots, new_slot_addresses=["192.168.10.30"], compose=_compose_settings())
    before = {p: p.read_bytes() for p in (compose, path, Path(rollout["slots"][0]["state_dir"]) / "config.json")}
    real, failed = module.atomic_private, []
    def fail_registration(target, value):
        if Path(target) == path and not failed:
            failed.append(True)
            if after_commit:
                real(target, value)
            raise OSError("fictional registration acknowledgement failure")
        return real(target, value)
    monkeypatch.setattr(module, "atomic_private", fail_registration)
    with pytest.raises(OSError):
        module.apply_and_register(path, rollout, plan)
    assert {p: p.read_bytes() for p in before} == before
    assert rollout == original
    assert (root / "nas-slot-1" / "device.key").is_file()  # Never discard a generated identity on error.


def test_rollout_transaction_blocks_a_profile_writer_until_it_settles(tmp_path, monkeypatch):
    _, _, path, report, revision = _locked_setup(tmp_path, monkeypatch)
    raw = json.loads(path.read_text())
    config = Path(raw["slots"][0]["state_dir"]) / "config.json"
    entered, release, attempting, acquired = (threading.Event() for _ in range(4))
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        raise module.RolloutError("fictional no-write refusal")
    monkeypatch.setattr(module, "apply_and_register", held)
    def apply():
        with pytest.raises(module.RolloutError):
            module.apply_if_current(path, report, revision)
    def profile_writer():
        attempting.set()
        with AiPortConfigurationStore(config)._locked():
            acquired.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply)
        assert entered.wait(5)
        second = pool.submit(profile_writer)
        try:
            assert attempting.wait(5)
            assert not acquired.wait(.2)
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert acquired.is_set()


def test_later_unowned_compose_edit_survives_and_recovery_is_retained(tmp_path, monkeypatch):
    root, compose, path, rollout, slots, report = _one_new_nas_slot(tmp_path)
    path.write_text(json.dumps(rollout))
    path.chmod(0o600)
    original = compose.read_bytes()
    plan = module.plan_rollout(report, slots, new_slot_addresses=["192.168.10.30"], compose=_compose_settings())
    real, failed = module.atomic_private, []
    def intervening(target, value):
        if Path(target) == path and not failed:
            failed.append(True)
            compose.write_bytes(b"# fictional later unowned edit\n")
            raise OSError("fictional lost registration acknowledgement")
        return real(target, value)
    monkeypatch.setattr(module, "atomic_private", intervening)
    with pytest.raises(module.RolloutOutcomeUncertain):
        module.apply_and_register(path, rollout, plan)
    assert compose.read_bytes() == b"# fictional later unowned edit\n"
    recovery = module._transaction_path(path)
    saved = json.loads(recovery.read_text())
    assert saved["state"] == "uncertain" and recovery.stat().st_mode & 0o077 == 0
    copy = next(row for row in saved["before"] if row["path"] == str(compose))
    assert base64.b64decode(copy["data"]) == original
    with pytest.raises(module.RolloutOutcomeUncertain):
        module.load_rollout_config(path)
    with pytest.raises(module.RolloutOutcomeUncertain):
        module.apply_and_register(path, rollout, plan)
    assert (root / "nas-slot-1" / "device.key").is_file()


def test_rollback_io_failure_keeps_verified_before_bytes_and_blocks_replay(tmp_path, monkeypatch):
    _, compose, path, rollout, slots, report = _one_new_nas_slot(tmp_path)
    path.write_text(json.dumps(rollout))
    path.chmod(0o600)
    before = compose.read_bytes()
    plan = module.plan_rollout(report, slots, new_slot_addresses=["192.168.10.30"], compose=_compose_settings())
    real, failed = module.atomic_private, []
    def refuse(target, value):
        if Path(target) == path and not failed:
            failed.append(True)
            raise OSError("fictional registry write failure")
        if Path(target) == compose and failed:
            raise OSError("fictional restore write failure")
        return real(target, value)
    monkeypatch.setattr(module, "atomic_private", refuse)
    with pytest.raises(module.RolloutOutcomeUncertain):
        module.apply_and_register(path, rollout, plan)
    assert compose.read_bytes() != before
    recovery = json.loads(module._transaction_path(path).read_text())
    assert recovery["state"] == "uncertain"
    copy = next(row for row in recovery["before"] if row["path"] == str(compose))
    assert base64.b64decode(copy["data"]) == before
    with pytest.raises(module.RolloutOutcomeUncertain):
        module.apply_and_register(path, rollout, plan)


async def test_uncertain_rollout_does_not_claim_nothing_changed(tmp_path, monkeypatch):
    from aikey.admin_audit import AuditLog
    from test_control_site_secrets import signed_in
    site, server, client, cookie, config = await signed_in(tmp_path)
    site.audit = AuditLog(tmp_path / "audit.jsonl")
    site.rollout_path = tmp_path / "rollout.json"
    async def inventory():
        return {}
    site.inventory_loader = inventory
    def uncertain(*args):
        raise module.RolloutOutcomeUncertain("fictional-private-canary")
    monkeypatch.setattr("aikey.control_site.apply_if_current", uncertain)
    try:
        response = await client.post('/aiport-rollout', data={"csrf": site.security.csrf_token(cookie),
            "revision": "0" * 64}, headers={"Origin": site.origin}, allow_redirects=False)
        assert response.status == 200
        body = await response.text()
        assert "Rollout outcome uncertain" in body and "No automatic retry" in body
        assert "No pairing was changed" not in body and "fictional-private-canary" not in body
        assert site.audit.recent()[0]["result"] == "uncertain"
    finally:
        await client.close()
        await server.close()

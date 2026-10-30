"""Maintenance transport owns real local runbooks, not a privileged web socket."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from aikey import admin_maintenance as module
from aikey.admin_audit import AuditLog
from test_admin_config_api import ROOT, headers
from test_control_site_secrets import signed_in


RESULT = 'print(json.dumps({"schema":"aikey-maintenance-result/1","outcome":"succeeded"}))\n'
RUNBOOK = '''import json, pathlib, shutil
value=json.load(__import__("sys").stdin)
source, backup=pathlib.Path(value["source"]), pathlib.Path(value["backup"])
if value["kind"]=="backup":
    shutil.copyfile(source, backup)
    assert source.read_bytes()==backup.read_bytes()
elif value["kind"]=="restore":
    target=pathlib.Path(value["isolated_target"])
    assert not target.exists() and target!=source
    shutil.copyfile(backup, target)
    assert target.read_bytes()==backup.read_bytes()
else:
    target=pathlib.Path(value["isolated_target"])
    target.write_text("fictional-installed-v2")
    assert target.read_text()=="fictional-installed-v2"
''' + RESULT


def prepared(tmp_path, *, code=RUNBOOK, kind="backup", timeout=5, configuration=None):
    host, bridge = tmp_path / "host", tmp_path / "bridge"
    config = configuration or tmp_path / "source.json"
    if configuration is None:
        config.write_text('{"fictional":"device-identity-unchanged"}\n')
    script = tmp_path / "approved.py"
    script.write_text(code)
    inputs = {"source": str(config), "backup": str(tmp_path / "cold-copy.json"),
              "isolated_target": str(tmp_path / "restored.json"), "kind": kind,
              "private_canary": "fictional-host-input-not-public"}
    revision = hashlib.sha256(config.read_bytes()).hexdigest()
    identifier = module.prepare(host, bridge, script=script, inputs=inputs, kind=kind,
        profile="aikey", revision=revision, timeout=timeout,
        expected_effect="read_only" if kind == "backup" else "isolated_restore",
        guard_files=[str(config)])
    return host, bridge, config, identifier, revision


def test_owned_backup_is_one_use_across_request_ids_and_public_data_has_no_host_inputs(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    client = module.Bridge(bridge)
    original = config.read_bytes()
    public = json.dumps(client.catalog())
    assert "fictional-host-input-not-public" not in public and str(tmp_path) not in public
    assert client.submit(identifier, revision, "1" * 32)["executed"] is False
    assert client.submit(identifier, revision, "1" * 32)["state"] == "queued"
    assert module.work_once(host, bridge) == {"handled": 1}
    assert client.receipt("1" * 32)["state"] == "succeeded"
    assert (tmp_path / "cold-copy.json").read_bytes() == original == config.read_bytes()
    (tmp_path / "cold-copy.json").write_bytes(b"later-owned-marker")
    client.submit(identifier, revision, "2" * 32)
    assert module.work_once(host, bridge) == {"handled": 1}
    assert client.receipt("2" * 32)["state"] == "succeeded"
    assert (tmp_path / "cold-copy.json").read_bytes() == b"later-owned-marker"
    assert module.work_once(host, bridge) == {"handled": 0}
    assert not list((bridge / "requests").glob(".pending-*"))


@pytest.mark.parametrize("kind", ["restore", "update"])
def test_other_approved_flows_execute_only_their_frozen_local_fixture(tmp_path, kind):
    (tmp_path / "cold-copy.json").write_bytes(b"fictional-snapshot")
    host, bridge, config, identifier, revision = prepared(tmp_path, kind=kind)
    before = config.read_bytes()
    module.Bridge(bridge).submit(identifier, revision, "3" * 32)
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("3" * 32)["state"] == "succeeded"
    assert config.read_bytes() == before
    assert (tmp_path / "restored.json").read_bytes() == (
        b"fictional-snapshot" if kind == "restore" else b"fictional-installed-v2")


def test_interrupted_started_plan_is_uncertain_and_never_reexecuted(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    module.Bridge(bridge).submit(identifier, revision, "4" * 32)
    (host / "ledger" / (identifier + ".json")).write_text('{"state":"started"}')
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("4" * 32)["state"] == "uncertain"
    assert not (tmp_path / "cold-copy.json").exists()


def test_changed_guard_refuses_before_runbook_effect(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    module.Bridge(bridge).submit(identifier, revision, "5" * 32)
    config.write_bytes(b"later-private-configuration")
    module.work_once(host, bridge)
    assert module.Bridge(bridge).receipt("5" * 32)["state"] == "rejected"
    assert not (tmp_path / "cold-copy.json").exists()


def test_uncertain_earlier_operation_blocks_a_different_new_plan(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    client = module.Bridge(bridge)
    client.submit(identifier, revision, "1" * 32)
    (host / "ledger" / (identifier + ".json")).write_text('{"state":"started"}')
    module.work_once(host, bridge)
    _, _, _, second, _ = prepared(tmp_path, kind="update")
    client.submit(second, revision, "2" * 32)
    result = module.work_once(host, bridge)
    assert result == {"handled": 0, "blocked": "prior_outcome_uncertain"}
    assert client.receipt("2" * 32)["state"] == "not_reported"
    assert not (tmp_path / "restored.json").exists()


def test_killed_host_owner_keeps_lease_in_runbook_then_reconciles_without_replay(tmp_path):
    code = '''import json, os, pathlib, time
v=json.load(__import__("sys").stdin)
pathlib.Path(v["backup"]).write_text(str(os.getpid()))
time.sleep(1)
pathlib.Path(v["isolated_target"]).write_text("completed-once-after-owner-loss")
''' + RESULT
    host, bridge, config, identifier, revision = prepared(tmp_path, code=code)
    client = module.Bridge(bridge)
    client.submit(identifier, revision, "3" * 32)
    env = dict(os.environ, PYTHONPATH=str(module.__file__).rsplit("/aikey/", 1)[0])
    owner = subprocess.Popen([sys.executable, "-m", "aikey.admin_maintenance", "work-once",
        "--host", str(host), "--bridge", str(bridge)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        deadline = time.monotonic() + 4
        while not (tmp_path / "cold-copy.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        child = int((tmp_path / "cold-copy.json").read_text())
        owner.kill()
        stdout, stderr = owner.communicate(timeout=2)
        assert not stdout and not stderr
        with pytest.raises(module.MaintenanceError, match="maintenance_worker_busy"):
            module.work_once(host, bridge)
        while not (tmp_path / "restored.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / "restored.json").read_text() == "completed-once-after-owner-loss"
        # Wait only for the inherited lease to close, never launch another runbook.
        while time.monotonic() < deadline:
            try:
                with module._owner(host):
                    break
            except module.MaintenanceError:
                time.sleep(.01)
        else:
            pytest.fail("Owned runbook did not release its inherited lease")
        assert module.work_once(host, bridge) == {"handled": 1}
        assert client.receipt("3" * 32)["state"] == "uncertain"
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=2)
        if child is not None:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("code", [
    'import time\ntime.sleep(30)\n',
    'import sys\nsys.stdout.write("fictional-private-output"*10000)\n',
    'print("fictional-private-exception")\nraise RuntimeError("fictional-private-exception")\n',
    'print(\'{"schema":"aikey-maintenance-result/1","outcome":"succeeded","outcome":"rejected"}\')\n',
])
def test_timeout_overflow_or_invalid_result_is_uncertain_with_fixed_receipt(tmp_path, code):
    host, bridge, config, identifier, revision = prepared(tmp_path, code=code, timeout=1)
    client = module.Bridge(bridge)
    client.submit(identifier, revision, "6" * 32)
    started = time.monotonic()
    module.work_once(host, bridge)
    assert time.monotonic() - started < 3
    result = client.receipt("6" * 32)
    assert result["state"] == "uncertain" and result["automatic_retry"] is False
    assert "fictional-private" not in json.dumps(result)


def test_host_worker_lock_prevents_overlapping_execution(tmp_path):
    code = '''import json, pathlib, time
v=json.load(__import__("sys").stdin)
pathlib.Path(v["backup"]).write_text("entered")
time.sleep(.8)
''' + RESULT
    host, bridge, config, identifier, revision = prepared(tmp_path, code=code)
    module.Bridge(bridge).submit(identifier, revision, "7" * 32)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(module.work_once, host, bridge)
        deadline = time.monotonic() + 3
        while not (tmp_path / "cold-copy.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / "cold-copy.json").exists()
        with pytest.raises(module.MaintenanceError, match="maintenance_worker_busy"):
            module.work_once(host, bridge)
        assert first.result(timeout=3) == {"handled": 1}


def test_setsid_descendant_holding_output_prevents_success_without_a_kill_claim(tmp_path):
    code = '''import json, os, pathlib, subprocess, sys, time
v=json.load(sys.stdin)
child="import os,pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path("+repr(v["backup"])+").write_text(str(os.getpid()));time.sleep(30)"
subprocess.Popen([sys.executable,"-c",child],start_new_session=True,
    pass_fds=(int(os.environ["AIKEY_MAINTENANCE_LEASE_FD"]),))
deadline=time.monotonic()+2
while not pathlib.Path(v["backup"]).exists() and time.monotonic()<deadline:time.sleep(.01)
''' + RESULT
    host, bridge, config, identifier, revision = prepared(tmp_path, code=code)
    client = module.Bridge(bridge)
    client.submit(identifier, revision, "4" * 32)
    child = None
    try:
        started = time.monotonic()
        module.work_once(host, bridge)
        assert time.monotonic() - started < 3
        child = int((tmp_path / "cold-copy.json").read_text())
        assert client.receipt("4" * 32)["state"] == "uncertain"
        # Escaped session is not falsely claimed killed; its inherited lease
        # still prevents a new owner, until this harness collects its fixture.
        with pytest.raises(module.MaintenanceError, match="maintenance_worker_busy"):
            module.work_once(host, bridge)
    finally:
        if child is None and (tmp_path / "cold-copy.json").exists():
            child = int((tmp_path / "cold-copy.json").read_text())
        if child is not None:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_request_cannot_replace_frozen_revision_or_reuse_id_for_different_plan(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    client = module.Bridge(bridge)
    with pytest.raises(module.MaintenanceError):
        client.submit(identifier, "0" * 64, "8" * 32)
    assert not list((bridge / "requests").iterdir())
    client.submit(identifier, revision, "8" * 32)
    path = bridge / "requests" / ("8" * 32 + ".json")
    before = path.read_bytes()
    # An existing ID with different content is not overwritten, even for the same plan.
    path.write_bytes(before + b" ")
    with pytest.raises(module.MaintenanceError, match="maintenance_request_conflict"):
        client.submit(identifier, revision, "8" * 32)
    assert path.read_bytes() == before + b" "


def test_withdrawn_catalog_revokes_queued_plan_before_any_host_effect(tmp_path):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    client = module.Bridge(bridge)
    client.submit(identifier, revision, "e" * 32)
    (bridge / "catalog" / (identifier + ".json")).unlink()
    module.work_once(host, bridge)
    assert client.receipt("e" * 32)["state"] == "rejected"
    assert not (tmp_path / "cold-copy.json").exists()


@pytest.mark.parametrize("field", ["kind", "profile", "expected_effect", "timeout", "revision"])
def test_malformed_host_metadata_is_not_echoed_or_admitted(tmp_path, field):
    host, bridge, config, identifier, revision = prepared(tmp_path)
    path = bridge / "catalog" / (identifier + ".json")
    value = json.loads(path.read_text())
    value[field] = {"fictional-private-canary": "not-an-enum"}
    path.write_text(json.dumps(value))
    with pytest.raises(module.MaintenanceError):
        module.Bridge(bridge).submit(identifier, revision, "f" * 32)
    assert not list((bridge / "requests").iterdir())


async def test_authenticated_queue_requires_audit_and_never_executes_in_web_process(tmp_path):
    site, server, client, cookie, config = await signed_in(tmp_path)
    host, bridge, _, identifier, revision = prepared(tmp_path, configuration=config)
    site.maintenance = module.Bridge(bridge)
    try:
        response = await client.get(ROOT + "/maintenance")
        assert response.status == 200 and len((await response.json())["operations"]) == 1
        payload = {"operation": identifier, "revision": revision, "request": "9" * 32}
        response = await client.post(ROOT + "/maintenance", json=payload, headers=headers(site, cookie))
        assert response.status == 503 and not list((bridge / "requests").iterdir())
        site.audit = AuditLog(tmp_path / "audit.jsonl")
        response = await client.post(ROOT + "/maintenance", json=payload, headers=headers(site, cookie))
        assert response.status == 202 and (await response.json())["executed"] is False
        assert not (tmp_path / "cold-copy.json").exists()
        assert site.audit.recent()[0]["result"] == "queued"
        admission = next(row for row in site.audit.recent() if row["result"] == "admitted")
        assert admission["operation_id"] == identifier and admission["request_id"] == "9" * 32
        response = await client.get(ROOT + "/maintenance/requests/" + "9" * 32)
        assert (await response.json())["state"] == "not_reported"
        module.work_once(host, bridge)
        response = await client.get(ROOT + "/maintenance/requests/" + "9" * 32)
        assert (await response.json())["state"] == "succeeded"
        response = await client.post(ROOT + "/maintenance", json={**payload, "command": "fictional"}, headers=headers(site, cookie))
        assert response.status == 400
        await client.post("/logout", data={"csrf": site.security.csrf_token(cookie)},
                          headers={"Origin": site.origin}, allow_redirects=False)
        response = await client.get(ROOT + "/maintenance")
        assert response.status == 401
    finally:
        await client.close()
        await server.close()

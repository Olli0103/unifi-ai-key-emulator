"""No real Docker/model/controller calls: writer lifetime and authority boundaries."""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from aikey.model_work_fence import FILENAME, ModelWorkFence, ModelWorkFenceError, initialize

SPEC = importlib.util.spec_from_file_location(
    "model_work_watchdog", Path(__file__).parents[1] / "tools/model_work_watchdog.py")
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def fixture(tmp_path):
    initialize(tmp_path, "0" * 32)
    fence = ModelWorkFence(tmp_path, enabled=True)
    legacy = tmp_path / "legacy.sh"
    legacy.write_text("#!/bin/bash\nexit 0\n")
    legacy.chmod(0o600)
    config = {"schema": watchdog.CONFIG_SCHEMA, "state": str(tmp_path),
        "legacy_script": str(legacy), "legacy_sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
        "provider": {"container": "model-fixture", "image": "sha256:"+"a"*64,
                     "url": "http://127.0.0.2:8000/v1/models", "model": "synthetic-only"},
        "ready_client": {"container": "reader-fixture", "image": "sha256:"+"b"*64},
        "participants": [{"container": "key-fixture", "image": "sha256:"+"c"*64,
                          "state": str(tmp_path), "kind": "key"}]}
    path = tmp_path / "watchdog.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    return fence, config, path


def test_busy_reader_cannot_even_start_probe_or_verification(tmp_path):
    fence, config, _ = fixture(tmp_path)
    reader = asyncio.run(fence.acquire())
    calls = []
    try:
        result = watchdog.execute(config, b"fixture", fence,
            verify_consumers=lambda *args: calls.append("verify"),
            execute_legacy=lambda *args: calls.append("legacy"),
            verify_ready=lambda *args: calls.append("ready"))
        assert result == {"status": "busy", "legacy_invoked": False}
        assert not calls
    finally:
        reader.close()
        fence.close()


def test_queued_consumer_gets_priority_before_a_probe(tmp_path):
    fence, config, _ = fixture(tmp_path)
    calls = []
    try:
        assert watchdog.execute(config, b"fixture", fence,
            verify_consumers=lambda *args: ({"Id": "fixture"}, True),
            execute_legacy=lambda *args: calls.append(True)) == {
                "status": "consumer_waiting", "legacy_invoked": False}
        assert not calls and json.loads((tmp_path/FILENAME).read_text())["phase"] == "idle"
    finally:
        fence.close()


def test_exclusive_lock_spans_probe_restart_and_ready_readback(tmp_path):
    fence, config, _ = fixture(tmp_path)
    calls = []
    peer = ModelWorkFence(tmp_path, enabled=True)

    def legacy(snapshot):
        assert snapshot == b"frozen fixture"
        assert peer.try_exclusive() is None
        assert json.loads((tmp_path/FILENAME).read_text())["phase"] == "maintenance"
        calls.append("legacy")

    def ready(config, before):
        assert before == {"Id": "fixture"}
        assert peer.try_exclusive() is None
        assert json.loads((tmp_path/FILENAME).read_text())["phase"] == "maintenance"
        calls.append("ready")

    try:
        value = watchdog.execute(config, b"frozen fixture", fence,
            verify_consumers=lambda *args: ({"Id": "fixture"}, False),
            execute_legacy=legacy, verify_ready=ready)
        assert value == {"status": "completed", "legacy_invoked": True, "participant_count": 1}
        assert calls == ["legacy", "ready"]
        lease = asyncio.run(peer.acquire())
        lease.close()
    finally:
        peer.close()
        fence.close()


@pytest.mark.parametrize("boundary", ["legacy", "readiness", "cancel"])
def test_failed_or_cancelled_writer_leaves_intent_and_cannot_replay(tmp_path, boundary):
    fence, config, _ = fixture(tmp_path)
    calls = []

    def legacy(*args):
        calls.append("legacy")
        if boundary == "legacy":
            raise watchdog.WatchdogError("synthetic")
        if boundary == "cancel":
            raise KeyboardInterrupt

    def ready(*args):
        calls.append("ready")
        raise watchdog.WatchdogError("synthetic")

    try:
        with pytest.raises((watchdog.WatchdogError, KeyboardInterrupt)):
            watchdog.execute(config, b"fixture", fence,
                verify_consumers=lambda *args: ({"Id": "fixture"}, False),
                execute_legacy=legacy, verify_ready=ready)
        previous = list(calls)
        assert json.loads((tmp_path/FILENAME).read_text())["phase"] == "maintenance"
        with pytest.raises(ModelWorkFenceError, match="unresolved"):
            watchdog.execute(config, b"fixture", fence,
                verify_consumers=lambda *args: pytest.fail("must not verify/repeat"),
                execute_legacy=legacy, verify_ready=ready)
        assert calls == previous and fence.status()["leases"] == 0
    finally:
        fence.close()


def test_unverified_participant_refuses_before_intent_or_probe(tmp_path):
    fence, config, _ = fixture(tmp_path)

    def refuse(*args):
        raise watchdog.WatchdogError("synthetic_unenrolled")

    try:
        with pytest.raises(watchdog.WatchdogError):
            watchdog.execute(config, b"fixture", fence, verify_consumers=refuse,
                             execute_legacy=lambda *args: pytest.fail("must not run"))
        assert json.loads((tmp_path/FILENAME).read_text())["phase"] == "idle"
    finally:
        fence.close()


def test_legacy_executes_frozen_anonymous_bytes_not_replaced_path(tmp_path):
    fence, config, path = fixture(tmp_path)
    original = tmp_path / "original-ran"
    replacement = tmp_path / "replacement-must-not-run"
    source = Path(config["legacy_script"])
    source.write_text(f'#!/bin/bash\nprintf original > "{original}"\n')
    config["legacy_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(config))
    try:
        _, snapshot = watchdog.load(path)
        source.write_text(f'#!/bin/bash\nprintf replacement > "{replacement}"\n')
        watchdog.run_legacy(snapshot, timeout=2)
        assert original.read_text() == "original" and not replacement.exists()
    finally:
        fence.close()


def test_legacy_failure_does_not_export_child_output(tmp_path, capsys):
    fence, _, _ = fixture(tmp_path)
    try:
        with pytest.raises(watchdog.WatchdogError, match="legacy_execution_failed"):
            watchdog.run_legacy(b'echo PRIVATE-CANARY; echo PRIVATE-ERROR >&2; exit 1\n', timeout=2)
        captured = capsys.readouterr()
        assert "PRIVATE" not in captured.out+captured.err
    finally:
        fence.close()


def test_legacy_timeout_is_bounded_and_direct_child_is_collected(tmp_path):
    fence, _, _ = fixture(tmp_path)
    pid_file = tmp_path / "pid"
    snapshot = f'echo $$ > "{pid_file}"\nexec sleep 20\n'.encode()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            watchdog.run_legacy(snapshot, timeout=0.1)
        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        fence.close()


@pytest.mark.parametrize("change", [
    lambda c: c.update(extra="refused"),
    lambda c: c.update(schema="future"),
    lambda c: c.update(state="relative"),
    lambda c: c.update(legacy_sha256="f"*64),
    lambda c: c["provider"].update(url="http://127.0.0.2:8000/v1/models?x=1"),
    lambda c: c["provider"].update(url="http://user:password@127.0.0.2:8000/v1/models"),
    lambda c: c["provider"].update(url="http://example.invalid/v1/models"),
    lambda c: c["provider"].update(url="http://0.0.0.0/v1/models"),
    lambda c: c["provider"].update(model="bad\nmodel"),
    lambda c: c["participants"][0].update(kind="unknown"),
    lambda c: c["participants"][0].update(container="valid\ninvalid"),
    lambda c: c["participants"][0].update(image="latest"),
    lambda c: c.update(participants=[]),
])
def test_configuration_refusal_before_any_execution(tmp_path, change, monkeypatch):
    fence, config, path = fixture(tmp_path)
    change(config)
    path.write_text(json.dumps(config))
    monkeypatch.setattr(watchdog, "_docker", lambda *a, **k: pytest.fail("must not execute"))
    try:
        with pytest.raises(watchdog.WatchdogError, match="configuration_refused"):
            watchdog.load(path)
        assert json.loads((tmp_path/FILENAME).read_text())["phase"] == "idle"
    finally:
        fence.close()


@pytest.mark.parametrize("fault", [None, "legacy", "different_inode", "wrong_mount", "image", "leases", "lock_visibility"])
def test_default_participant_verification_binds_images_mounts_and_retained_inode(tmp_path, monkeypatch, fault):
    fence, config, _ = fixture(tmp_path)
    if fault == "different_inode":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        initialize(other, "1"*32)
        config["participants"][0]["state"] = str(other)
    provider = {"Id": "provider-id", "Image": config["provider"]["image"],
                "State": {"Running": True, "Pid": 1},
                "NetworkSettings": {"Networks": {"fixture": {"IPAddress": "127.0.0.2"}}}}
    client = {"Id": "client-id", "Image": config["ready_client"]["image"], "State": {"Running": True}}
    consumer = {"Id": "consumer-id", "Image": config["participants"][0]["image"],
                "State": {"Running": True, "Pid": 2},
                "Mounts": [{"Destination": "/state", "Source": config["participants"][0]["state"]}]}
    if fault == "wrong_mount":
        consumer["Mounts"][0]["Source"] = "/wrong"
    if fault == "image":
        consumer["Image"] = "sha256:"+"f"*64
    health = {**fence.status(), "leases": 0, "maintenance": "writer_active"}
    if fault == "legacy":
        health["enabled"] = False
    if fault == "leases":
        health["leases"] = 1
    if fault == "lock_visibility":
        health["maintenance"] = "idle"
    rows = {"model-fixture": provider, "reader-fixture": client, "key-fixture": consumer}

    def docker(*args, code=None, **kwargs):
        if args[0] == "inspect":
            return [rows[args[1]]]
        assert args[:3] == ("exec", "-i", "consumer-id")
        assert b"'/healthz'" in code and b"model_work_fence" in code
        return health

    monkeypatch.setattr(watchdog, "_docker", docker)
    writer = fence.try_exclusive()
    try:
        if fault:
            with pytest.raises(watchdog.WatchdogError):
                watchdog.verify(config, fence)
        else:
            assert watchdog.verify(config, fence) == (provider, False)
    finally:
        writer.close()
        fence.close()


def test_late_metadata_success_does_not_become_ready(tmp_path, monkeypatch):
    fence, config, _ = fixture(tmp_path)
    now = [0.0]
    provider = {"Id": "provider-id", "State": {"Running": True},
                "NetworkSettings": {"Networks": {"fixture": {"IPAddress": "127.0.0.2"}}}}
    monkeypatch.setattr(watchdog, "time", SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda delay: None))
    monkeypatch.setattr(watchdog, "_inspect", lambda row, **kwargs: provider)

    def late(*args, code=None, **kwargs):
        assert b"/v2/models/" in code and b"/ready" in code
        now[0] = 21
        return {"ready": True}

    monkeypatch.setattr(watchdog, "_docker", late)
    try:
        with pytest.raises(watchdog.WatchdogError, match="readiness_unverified"):
            watchdog.ready(config, provider, timeout=20)
    finally:
        fence.close()

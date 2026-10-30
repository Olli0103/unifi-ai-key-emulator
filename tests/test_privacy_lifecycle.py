"""Cross-component privacy lifecycle checks; generated state and owned processes only.

These exercise existing mechanisms, not remote erasure or live revocation.
"""

import asyncio
import json
import stat
import sys
from types import SimpleNamespace

import pytest

from aikey.config import atomic_private, hydrate_secrets, load_config
from aikey.device import DeviceService
from aikey.faces import FaceStore
from aikey.providers import VisionProvider
from aikey.protocol import decode_message
from aikey.worker import JobProcessor, WorkerError
from test_config_store import store as key_store
from test_device import request
from test_role_models import key_store as speech_store
from test_speech_to_text import task as speech_task


async def no_admission(body):
    raise AssertionError("Opted-out work must not reach the handler")


def immutable_files(root):
    names = ("identity.json", "management-password", "database-password",
             "device.crt", "device.key", "device-state.json")
    return {name: (root / name).read_bytes() for name in names if (root / name).exists()}


def test_provider_key_rotation_and_undo_preserve_an_adopted_device(tmp_path):
    store, path = key_store(tmp_path)
    root = tmp_path / "state"
    atomic_private(root / "provider-key", "fictional-old-vision-key\n")
    original = load_config(path)
    hydrated = hydrate_secrets(original)
    device = DeviceService(hydrated, root, no_admission)
    # Existing adopted fixture, not a fabricated native/controller adoption claim.
    device._state["adopted"] = True
    device._save_state()
    immutable = immutable_files(root)
    config_before = path.read_bytes()
    new = root / "provider-key-new"
    atomic_private(new, "fictional-new-vision-key\n")
    current = store.apply_inference(store.snapshot().revision, {
        **original["inference"], "api_key_file": str(new)})
    after = hydrate_secrets(load_config(path))
    assert after["inference"]["api_key"] == "fictional-new-vision-key"
    assert after["device"]["management_password"] == hydrated["device"]["management_password"]
    provider = VisionProvider(after["inference"])
    _, headers, _ = provider.build_request([b"\x89PNG\r\n\x1a\nsynthetic-image"], "synthetic")
    assert headers["Authorization"] == "Bearer fictional-new-vision-key"
    reloaded = DeviceService(after, root, no_admission)
    assert reloaded.status["adopted"] and reloaded.mac == device.mac
    assert immutable_files(root) == immutable
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    assert (root / "provider-key").read_text() == "fictional-old-vision-key\n"
    # Rotation is a reference switch, not remote revocation or old-copy deletion.
    archived = [p.read_bytes() for p in store.history_dir.glob("*.json")]
    assert any(config_before == content for content in archived)
    assert "fictional-new-vision-key" not in repr(store.snapshot())
    store.rollback_provider(current.revision)
    assert path.read_bytes() == config_before and immutable_files(root) == immutable
    assert hydrate_secrets(load_config(path))["inference"]["api_key"] == "fictional-old-vision-key"
    assert new.exists()  # Do not orphan-delete a key another retained revision can need.


async def test_management_rotation_failure_then_success_preserves_adoption_and_identity(tmp_path):
    _, path = key_store(tmp_path)
    root = tmp_path / "state"
    atomic_private(root / "provider-key", "fictional-provider-key\n")
    config = hydrate_secrets(load_config(path))
    device = DeviceService(config, root, no_admission)
    device._state["adopted"] = True
    device._save_state()
    immutable = immutable_files(root)
    saved_state = device.state_path.read_bytes()
    body = {"username": config["device"]["management_username"],
            "passwordOld": config["device"]["management_password"],
            "passwordNew": "fictional-replacement-management-passphrase"}

    async def unavailable(username, password):
        raise OSError("synthetic downstream credential failure")

    device.credential_handler = unavailable
    failed = decode_message(await device.handle_message(request("changeUserPassword", body)))
    assert failed.header["errorCode"] != 0
    assert device.state_path.read_bytes() == saved_state
    assert device._password_matches(body["username"], body["passwordOld"])
    calls = []

    async def accepted(username, password):
        calls.append((username, password))

    device.credential_handler = accepted
    reply = decode_message(await device.handle_message(request("changeUserPassword", body, "retry")))
    assert reply.header["errorCode"] == 0
    assert calls == [(body["username"], body["passwordNew"])]
    restarted = DeviceService(config, root, no_admission)
    assert restarted.status["adopted"] and restarted.mac == device.mac
    assert restarted._password_matches(body["username"], body["passwordNew"])
    assert not restarted._password_matches(body["username"], body["passwordOld"])
    assert {k: v for k, v in immutable_files(root).items() if k != "device-state.json"} == {
        k: v for k, v in immutable.items() if k != "device-state.json"}
    assert body["passwordNew"].encode() not in device.state_path.read_bytes()
    assert body["passwordNew"] not in json.dumps(restarted.status)


async def test_saved_audio_opt_out_takes_effect_on_reconstruction_not_the_old_runtime(tmp_path):
    store, path = speech_store(tmp_path)
    atomic_private(tmp_path / "state/provider-key", "fictional-vision-key\n")
    before = hydrate_secrets(load_config(path))
    root = tmp_path / "state"
    old = DeviceService(before, root, no_admission)
    assert "speechToText" in old.status["supported_commands"]
    identity = immutable_files(root)
    store.restrict_speech(store.snapshot().revision, [])
    assert "speechToText" in old.status["supported_commands"]  # Explicit saved-only boundary.
    after = hydrate_secrets(load_config(path))
    fresh = DeviceService(after, root, no_admission)
    assert "speechToText" not in fresh.status["supported_commands"]
    worker = JobProcessor(after, root)
    try:
        with pytest.raises(WorkerError):
            await worker.submit(speech_task())
        assert worker._session is None and worker._inference_session is None
        assert not worker._tasks
        assert immutable_files(root) == identity
    finally:
        await worker.stop()


@pytest.mark.parametrize("kind", ["audio", "video"])
@pytest.mark.parametrize("outcome", ["success", "decoder_error", "cancel"])
async def test_per_job_media_is_removed_and_decoder_reaped(tmp_path, kind, outcome):
    # Use an owned real OS process rather than mocking TemporaryDirectory/kill.
    executable = tmp_path / "synthetic-decoder"
    ready = tmp_path / "decoder-started"
    executable.write_text(f'''#!{sys.executable}
from pathlib import Path
import sys, time
output=Path(sys.argv[-1])
assert b"fictional-private-media" in (output.parent / "input.mp4").read_bytes()
Path({str(ready)!r}).write_text("ready")
output.write_bytes(b"RIFF" + b"x" * 64 if output.suffix == ".wav" else b"\\xff\\xd8\\xffsynthetic")
if {outcome!r} == "cancel":
    while True: time.sleep(.02)
sys.exit(2 if {outcome!r} == "decoder_error" else 0)
''')
    executable.chmod(0o700)
    options = {"runtime": {"mode": "lab"}, "device": {"mac": "020000000099"},
               "controller_origins": ["http://127.0.0.1:1"],
               "inference": {"provider": "openai-compatible", "model": "synthetic",
                             "base_url": "http://127.0.0.1:1/v1"},
               "worker": {"ffmpeg_path": str(executable)}}
    worker = JobProcessor(options, tmp_path / "worker")
    data = b"\x00\x00\x00\x18ftypisomfictional-private-media"
    real_spawn = asyncio.create_subprocess_exec
    processes = []
    # Record the owned handle; retain actual spawn/wait/kill behavior.
    async def observed_spawn(*args, **kwargs):
        p = await real_spawn(*args, **kwargs)
        processes.append(p)
        return p

    from unittest.mock import patch
    task = None
    try:
        with patch("aikey.worker.asyncio.create_subprocess_exec", observed_spawn):
            if kind == "audio":
                call = worker._audio(data)
            else:
                job = SimpleNamespace(operation="recognizeKeyFrames", payload={"end": 1000})
                call = worker._video_frame(data, {}, "http://127.0.0.1:1/export", job)
            task = asyncio.create_task(call)
            if outcome == "cancel":
                async with asyncio.timeout(5):
                    while not ready.exists():
                        await asyncio.sleep(.01)
                directories = list(worker.state_dir.glob("aikey-*-*"))
                assert len(directories) == 1
                assert stat.S_IMODE(directories[0].stat().st_mode) == 0o700
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "decoder_error":
                with pytest.raises(WorkerError):
                    await task
            else:
                assert isinstance(await task, bytes)
        assert ready.exists() and len(processes) == 1
        assert processes[0].returncode is not None
        assert list(worker.state_dir.glob("aikey-*-*")) == []
        assert worker._session is worker._inference_session is None
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await worker.stop()


def test_identity_delete_is_durable_but_does_not_purge_a_separate_backup(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    atomic_private(state / "identity.json", json.dumps({"mac": "020000000099"}))
    store = FaceStore(state)
    first, other = [1.0] + [0.0] * 127, [0.0, 1.0] + [0.0] * 126
    store.enroll("Fictional Removed", first)
    store.enroll("Fictional Retained", other)
    backup = tmp_path / "operator-backup.json"
    atomic_private(backup, store.path.read_bytes())
    original_backup = backup.read_bytes()
    immutable = immutable_files(state)
    assert store.delete("Fictional Removed") is True
    assert store.delete("Fictional Removed") is False
    fresh = FaceStore(state)
    assert fresh.match(first) == (None, [])
    assert fresh.names() == ["Fictional Retained"]
    assert fresh.match(other)[0] == "Fictional Retained"
    assert b"Fictional Removed" not in fresh.path.read_bytes()
    assert immutable_files(state) == immutable
    fresh.purge()
    assert FaceStore(state).names() == [] and not fresh.path.exists()
    assert backup.read_bytes() == original_backup and immutable_files(state) == immutable
    # A restore is a new disclosure decision, not proof of deletion everywhere.
    restored = tmp_path / "isolated-restored-state"
    restored.mkdir(mode=0o700)
    face_copy = FaceStore(restored)
    face_copy.directory.mkdir(mode=0o700)
    atomic_private(face_copy.path, original_backup)
    assert face_copy.match(first)[0] == "Fictional Removed"

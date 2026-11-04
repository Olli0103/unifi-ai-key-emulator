"""Real owned processes/locks; never invokes the Apple VM runtime in fixtures."""

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from aikey import host_runtime_command as helper

ENTRY = Path(helper.__file__).resolve()
BOOTSTRAP = """
import importlib.util,sys
spec=importlib.util.spec_from_file_location('owned_guardian',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
# Test adapter permits only the owned Python fixture, leaving the actual
# parser, lease checks, cancellation, child watcher and collection unchanged.
module.allowed=lambda argv: argv[:3]==[sys.executable,'-I','-c']
module.mutating=lambda argv: True
raise SystemExit(module.main(sys.argv[2:]))
"""


def lease(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def fixture_command(program, descriptor, timeout=2):
    return [
        sys.executable,
        "-I",
        "-c",
        BOOTSTRAP,
        str(ENTRY),
        "--parent-pid",
        str(os.getpid()),
        "--timeout",
        str(timeout),
        "--lease-fd",
        str(descriptor),
        "--",
        sys.executable,
        "-I",
        "-c",
        program,
    ]


@pytest.mark.parametrize(
    "program,timeout,reason",
    [
        ('print("synthetic-complete")', 2, None),
        ('import time;time.sleep(10);print("must-not-accept-late")', 0.15, "timeout"),
        ('import sys;sys.stdout.write("x"*2097152);sys.stdout.flush()', 2, "output_limit"),
    ],
)
def test_actual_guardian_owns_deadline_output_and_collection(tmp_path, program, timeout, reason):
    descriptor = lease(tmp_path / "host.lock")
    try:
        result = subprocess.run(
            fixture_command(program, descriptor, timeout),
            pass_fds=(descriptor,),
            capture_output=True,
            timeout=5,
        )
    finally:
        os.close(descriptor)
    assert result.returncode == 0 and not result.stderr
    value = json.loads(result.stdout)
    assert value["schema"] == helper.SCHEMA and value["error"] == reason
    assert value["spawned"] is True and value["command_terminal"] is True
    if reason is None:
        assert value["exit_code"] == 0 and value["output"] == "synthetic-complete\n"
    else:
        assert value["output"] == "" and value["exit_code"] != 0


@pytest.mark.parametrize(
    "command",
    [
        ["/bin/sh", "-c", "exit 0"],
        ["/usr/local/bin/container", "start", "must-not-start"],
        ["/usr/local/bin/container", "image", "delete", "must-not-delete"],
    ],
)
def test_production_entrypoint_refuses_unleased_mutation_and_unknown_command(command):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ENTRY),
            "--parent-pid",
            str(os.getpid()),
            "--timeout",
            "1",
            "--",
            *command,
        ],
        capture_output=True,
        timeout=3,
    )
    assert result.returncode == 2 and not result.stdout and not result.stderr


def wait_file(path, deadline=5):
    until = time.monotonic() + deadline
    while not path.exists():
        assert time.monotonic() < until, "owned fixture did not enter its declared phase"
        time.sleep(0.01)


def test_parent_sigkill_does_not_release_authority_before_the_real_cli_is_collected(tmp_path):
    root_lock = tmp_path / "host.lock"
    child_lock = tmp_path / "child-alive.lock"
    entered = tmp_path / "entered"
    receipt = tmp_path / "guardian-result.json"
    program = (
        "import fcntl,os,time,pathlib;"
        f"fd=os.open({str(child_lock)!r},os.O_CREAT|os.O_RDWR,0o600);"
        "fcntl.flock(fd,fcntl.LOCK_EX);"
        f'pathlib.Path({str(entered)!r}).write_text("entered");time.sleep(30)'
    )
    actor_code = """
import fcntl,os,pathlib,subprocess,sys,time
root,entry,bootstrap,program,receipt=sys.argv[1:]
fd=os.open(root,os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX)
args=[sys.executable,'-I','-c',bootstrap,entry,'--parent-pid',str(os.getpid()),'--timeout','10',
      '--lease-fd',str(fd),'--',sys.executable,'-I','-c',program]
with open(receipt,'wb') as output:
 child=subprocess.Popen(args,pass_fds=(fd,),stdout=output,stderr=subprocess.DEVNULL,start_new_session=True)
 child.wait()
"""
    actor = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            actor_code,
            str(root_lock),
            str(ENTRY),
            BOOTSTRAP,
            program,
            str(receipt),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    probe = None
    try:
        wait_file(entered)
        probe = os.open(root_lock, os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.kill(actor.pid, signal.SIGKILL)
        actor.wait(timeout=3)
        until = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.monotonic() < until, "guardian still owns authority"
                time.sleep(0.01)
        # This fixture keeps its own kernel lock for its whole lifetime. No
        # PID-liveness guess or signal to a potentially reused PID is needed.
        descriptor = os.open(child_lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        value = json.loads(receipt.read_text())
        assert value["error"] == "owner_lost" and value["spawned"] is True
        assert value["command_terminal"] is True and value["exit_code"] != 0
    finally:
        if actor.poll() is None:
            actor.kill()
            actor.wait(timeout=3)
        if probe is not None:
            os.close(probe)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS interface-reader entrypoint")
def test_actual_production_readonly_entrypoint_on_mac_does_not_need_a_mutation_lease():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ENTRY),
            "--parent-pid",
            str(os.getpid()),
            "--timeout",
            "2",
            "--",
            "/sbin/ifconfig",
        ],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0 and not result.stderr
    value = json.loads(result.stdout)
    assert value["error"] is None and value["exit_code"] == 0 and value["command_terminal"] is True
    assert "lo0:" in value["output"]


@pytest.mark.parametrize("owner_lost", [False, True])
def test_actual_guardian_publishes_exact_effect_context_without_native_payload(
    tmp_path, owner_lost
):
    marker = tmp_path / "fixture-executed"
    program = f'import pathlib;pathlib.Path({str(marker)!r}).write_text("yes");print("synthetic-private-native-output")'
    command = [sys.executable, "-I", "-c", program]
    descriptor = lease(tmp_path / "host.lock")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    info = os.fstat(root)
    context = {
        "operation": "1" * 32,
        "action": "run_new",
        "authority": "f" * 64,
        "authority_device": info.st_dev,
        "authority_inode": info.st_ino,
        "argv_sha256": helper.argv_digest(command),
    }
    args = fixture_command(program, descriptor)
    if owner_lost:
        args[args.index("--parent-pid") + 1] = str(os.getpid() + 1000000)
    index = args.index("--")
    args[index:index] = ["--root-fd", str(root), "--effect", json.dumps(context)]
    try:
        result = subprocess.run(args, pass_fds=(descriptor, root), capture_output=True, timeout=5)
        assert result.returncode == 0 and not result.stderr
        body = json.loads(result.stdout)
        assert body["effect_saved"] is True
        saved = helper.read_effect(tmp_path, context)
        assert saved["spawned"] is not owner_lost and saved["command_terminal"] is True
        assert saved["error"] == ("owner_lost" if owner_lost else None)
        assert marker.exists() is not owner_lost
        raw = (tmp_path / helper.effect_name(context)).read_bytes()
        assert b"synthetic-private-native-output" not in raw and "output" not in saved
        # Existing exact slot is not permission to replay the same action.
        again = subprocess.run(args, pass_fds=(descriptor, root), capture_output=True, timeout=5)
        assert (
            again.returncode == 2 and (tmp_path / helper.effect_name(context)).read_bytes() == raw
        )
    finally:
        os.close(root)
        os.close(descriptor)


@pytest.mark.parametrize("fault", ["source_digest", "bool_device", "not_terminal", "unknown_field"])
def test_effect_receipt_mismatch_is_not_no_execution(tmp_path, fault):
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    info = os.fstat(root)
    context = {
        "operation": "2" * 32,
        "action": "stop_old",
        "authority": "f" * 64,
        "authority_device": info.st_dev,
        "authority_inode": info.st_ino,
        "argv_sha256": "a" * 64,
    }
    try:
        helper.publish_effect(
            root,
            context,
            {"spawned": True, "command_terminal": True, "exit_code": 0, "error": None},
        )
    finally:
        os.close(root)
    path = tmp_path / helper.effect_name(context)
    value = json.loads(path.read_text())
    if fault == "source_digest":
        value["argv_sha256"] = "b" * 64
    if fault == "bool_device":
        value["authority_device"] = True
    if fault == "not_terminal":
        value["command_terminal"] = False
    if fault == "unknown_field":
        value["opaque"] = "synthetic-private-canary"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        helper.read_effect(tmp_path, context)

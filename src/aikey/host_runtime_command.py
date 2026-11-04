"""Stdlib-only local CLI guardian; retain the host lease until child collection.

Invoked by absolute package path with Python -I. Never a shell, provider worker
or generic untrusted process sandbox. Its CLI accepts only the existing local
Apple runtime / interface-reader and local-socket NAS Docker commands, not arbitrary executables.
"""
import argparse
import fcntl
import importlib.util
import json
import hashlib
import math
import os
from pathlib import Path
import selectors
import re
import secrets
import signal
import stat
import subprocess
import time

LIMIT = 1024 * 1024
SCHEMA = 'aikey-host-command/1'
RESULT_SCHEMA = 'aikey-host-command-result/1'
_HASH = re.compile('[0-9a-f]{64}\\Z')
_OP = re.compile('[0-9a-f]{32}\\Z')
_ACTION = re.compile(r'(?:stop_old|run_new|create_new|start_new|stop_new|delete_new|restart_old|recover_(?:stop_new|delete_new|restart_old)_[123]|nas_(?:create|start|stop)_(?:[1-9]|1[0-6]))\Z')
ERRORS = {'owner_lost', 'cancelled', 'timeout', 'output_limit', 'exec_failed', 'output_refused', 'observation_failed'}


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('ambiguous_json')
        value[key] = item
    return value


def argv_digest(argv):
    return hashlib.sha256(json.dumps(argv, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def checked_effect(value):
    if (not isinstance(value, dict) or set(value) != {
            'operation', 'action', 'authority', 'authority_device', 'authority_inode', 'argv_sha256'}
            or not isinstance(value['operation'], str) or not _OP.fullmatch(value['operation'])
            or not isinstance(value['action'], str) or not _ACTION.fullmatch(value['action'])
            or not all(isinstance(value[key], str) and _HASH.fullmatch(value[key]) for key in ('authority', 'argv_sha256'))
            or type(value['authority_device']) is not int or value['authority_device'] < 0
            or type(value['authority_inode']) is not int or value['authority_inode'] <= 0):
        raise ValueError('effect_context_refused')
    return value


def effect_name(value):
    checked_effect(value)
    return '.command-' + value['operation'] + '-' + value['action'] + '.json'


def verify_root(descriptor, effect):
    info = os.fstat(descriptor)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or (info.st_dev, info.st_ino) != (effect['authority_device'], effect['authority_inode'])):
        raise ValueError('effect_root_refused')


def publish_effect(descriptor, effect, result):
    """Create-only durable metadata; never persist native stdout/environment."""
    verify_root(descriptor, effect)
    name = effect_name(effect)
    temporary = name + '.' + secrets.token_hex(6) + '.tmp'
    value = {'schema': RESULT_SCHEMA, **effect,
             **{key: result[key] for key in ('spawned', 'command_terminal', 'exit_code', 'error')}}
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    try:
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(handle, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        os.unlink(temporary, dir_fd=descriptor)
        os.fsync(descriptor)
        verify_root(descriptor, effect)
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(handle, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size != len(raw)
                    or source.read(4097) != raw):
                raise ValueError('effect_publication_uncertain')
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def read_effect(directory, expected):
    """Parent-side exact receipt reader; unsafe/stale data is not no execution."""
    from .private_files import read_private as _private
    from .protocol import _json_object
    checked_effect(expected)
    try:
        raw = _private(Path(directory) / effect_name(expected), 4096)
    except FileNotFoundError:
        return None
    value = _json_object(raw)
    checked_effect({key: value[key] for key in expected})
    if (set(value) != {'schema', *expected, 'spawned', 'command_terminal', 'exit_code', 'error'}
            or value['schema'] != RESULT_SCHEMA or any(value[key] != item for key, item in expected.items())
            or type(value['spawned']) is not bool or value['command_terminal'] is not True
            or value['error'] is not None and value['error'] not in ERRORS
            or value['spawned'] and (type(value['exit_code']) is not int or not -255 <= value['exit_code'] <= 255)
            or not value['spawned'] and (value['exit_code'] is not None
                                       or value['error'] not in {'owner_lost', 'cancelled', 'exec_failed'})):
        raise ValueError('host_command_effect_refused')
    return value


def allowed(argv):
    if not isinstance(argv, list) or not argv or len(argv) > 256:
        return False
    if not all(isinstance(item, str) and 0 < len(item) <= 8192 and '\x00' not in item for item in argv):
        return False
    if argv[0] == '/usr/bin/docker':
        if argv[1:3] != ['--host', 'unix:///var/run/docker.sock']:
            return False
        argv = [argv[0], *argv[3:]]
        if argv[1:2] == ['start'] and len(argv) == 3:
            return bool(re.fullmatch('[0-9a-fA-F]{12,64}', argv[2]))
        if argv[1:2] == ['exec'] and len(argv) == 7:
            return (bool(re.fullmatch('[0-9a-fA-F]{12,64}', argv[2]))
                    and argv[3:5] == ['python', '-c'] and bool(_HASH.fullmatch(argv[6])))
        if argv[1:4] == ['image', 'inspect', '--format'] and len(argv) == 6:
            return argv[4] == '{{json .Config.Labels}}' and bool(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}', argv[5]))
        if argv[1:4] == ['stop', '--time', '10'] and len(argv) == 5:
            return bool(re.fullmatch('[0-9a-fA-F]{12,64}', argv[-1]))
        if argv[1:3] == ['inspect', '--format'] and len(argv) == 5:
            return bool(re.fullmatch('[0-9a-fA-F]{12,64}', argv[-1]))
        if argv[1:4] == ['network', 'inspect', '--format'] and len(argv) == 6:
            return bool(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', argv[-1]))
        if (len(argv) >= 8 and argv[1:3] == ['compose', '--project-name']
                and re.fullmatch('[a-z0-9][a-z0-9_-]{0,62}', argv[3])
                and argv[4] == '-f' and Path(argv[5]).is_absolute()):
            tail = argv[6:]
            return (tail == ['config', '--quiet'] or tail == ['ps', '--all', '--format', 'json']
                    or tail[:-1] == ['create', '--no-recreate', '--no-build', '--pull', 'never']
                    and bool(re.fullmatch('aiport_slot_[0-9]+', tail[-1])))
        return False
    if argv == ['/sbin/ifconfig']:
        return True
    if argv[0] != '/usr/local/bin/container' or len(argv) < 2:
        return False
    if argv[1] == 'image':
        return argv[1:3] == ['image', 'inspect'] and len(argv) == 4
    if argv[1] == 'list':
        return argv[1:] == ['list', '--all', '--format', 'json']
    if argv[1] == 'system':
        return argv[1:] in (['system', 'status'], ['system', 'start', '--disable-kernel-install', '--timeout', '60'])
    return argv[1] in {'run', 'create', 'start', 'stop', 'delete'}


def mutating(argv):
    return not (argv == ['/sbin/ifconfig'] or argv[1:2] == ['list']
                or argv[1:3] == ['image', 'inspect'] or argv[1:3] == ['system', 'status'])


def verify_lease(descriptor):
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError('lease_refused')
    # Same inherited open-file description: this does not release/reacquire
    # the actor's lease, and the guardian keeps it open through child wait().
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _watcher(pid):
    spec = importlib.util.spec_from_file_location('owned_process_exit', Path(__file__).with_name('process_exit.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not module.supported():
        raise OSError('exit_watcher_unavailable')
    return module.ExitWatcher(pid)


def execute(argv, *, parent_pid, timeout, cancelled):
    """Core used with owned fixture commands in tests; main enforces CLI policy."""
    child = watcher = None
    output = bytearray()
    reason = None
    status = None
    spawned = False
    deadline = time.monotonic() + timeout

    def stop_group():
        # The direct child is NOT reaped before this call (ExitWatcher uses
        # WNOWAIT/kqueue), so its process-group anchor cannot be PID-reused.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False  # Never mistake a live signal refusal for collection.
        return True

    try:
        if os.getppid() != parent_pid:
            reason = 'owner_lost'
        elif cancelled():
            reason = 'cancelled'
        else:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            spawned = True
            watcher = _watcher(child.pid)
            os.set_blocking(child.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                exited = False
                stopped = False
                exit_seen = None
                while True:
                    if not exited and watcher.exited():
                        exited = True
                        exit_seen = time.monotonic()
                    if reason is None:
                        if os.getppid() != parent_pid:
                            reason = 'owner_lost'
                        elif cancelled():
                            reason = 'cancelled'
                        elif time.monotonic() >= deadline:
                            reason = 'timeout'
                    if reason is not None and not exited and not stopped:
                        stopped = stop_group()
                    if exited and not selector.get_map():
                        break
                    if exited and time.monotonic() - exit_seen >= 0.25:
                        # The direct CLI is done. A descendant holding stdout
                        # must not create an unbounded output/authority wait.
                        # Keep the unreaped anchor while signalling the group;
                        # reject rather than accept an incomplete stream.
                        stop_group()
                        reason = reason or 'output_refused'
                        break
                    for key, _ in selector.select(0.05):
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if reason is None:
                            if len(output) + len(chunk) > LIMIT:
                                reason = 'output_limit'
                                output.clear()
                            else:
                                output.extend(chunk)
            status = child.wait()
    except (OSError, ValueError, RuntimeError):
        reason = reason or ('observation_failed' if spawned else 'exec_failed')
    finally:
        if child is not None:
            # Even watcher/output failure must collect the owned CLI before
            # the guardian releases its inherited kernel lease. A truly
            # unkillable kernel task stays fenced, never falsely "cleaned".
            if status is None:
                if watcher is None:
                    while status is None:
                        stop_group()
                        try:
                            status = child.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            pass
                else:
                    while not watcher.exited():
                        stop_group()
                        time.sleep(0.05)
                    status = child.wait()
            child.stdout.close()
        if watcher is not None:
            watcher.close()
    try:
        text = bytes(output).decode('utf-8') if reason is None else ''
    except UnicodeError:
        text, reason = '', 'output_refused'
    return {'schema': SCHEMA, 'spawned': spawned, 'command_terminal': True,
            'exit_code': status, 'error': reason, 'output': text}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('--lease-fd', type=int)
    parser.add_argument('--root-fd', type=int)
    parser.add_argument('--effect')
    parser.add_argument('--engine')
    parser.add_argument('--engine-sha256')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if (not allowed(command) or args.parent_pid <= 1 or not math.isfinite(args.timeout)
            or not 0 < args.timeout <= 90 or mutating(command) and args.lease_fd is None):
        return 2
    actual = command
    if args.engine is not None or args.engine_sha256 is not None:
        # Explicitly frozen host-approved adapter, not PATH executable lookup.
        # The normal CLI has no such option; only the prepared host runbook
        # supplies this digest-bound executor. Effects bind its actual argv.
        try:
            if (args.engine is None or args.engine_sha256 is None or command[0] != '/usr/local/bin/container'
                    or not _HASH.fullmatch(args.engine_sha256)
                    or not Path(args.engine).is_absolute() or str(Path(args.engine).resolve()) != args.engine):
                return 2
            spec = importlib.util.spec_from_file_location('owned_hash', Path(__file__).with_name('maintenance_hash.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if module.artifact_digest(args.engine, 64 * 1024 * 1024) != args.engine_sha256:
                return 2
            actual = [args.engine, *command[1:]]
        except (OSError, ValueError, TypeError):
            return 2
    if args.lease_fd is not None:
        try:
            verify_lease(args.lease_fd)
        except (OSError, ValueError):
            return 2
    effect = None
    if args.effect is not None or args.root_fd is not None:
        try:
            if args.effect is None or len(args.effect) > 4096 or args.root_fd is None or args.lease_fd is None:
                return 2
            effect = checked_effect(json.loads(args.effect, object_pairs_hook=unique_pairs))
            verify_root(args.root_fd, effect)
            if effect['argv_sha256'] != argv_digest(actual):
                return 2
            try:
                os.stat(effect_name(effect), dir_fd=args.root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return 2  # Never replay a mutation with an existing result slot.
        except (OSError, ValueError, TypeError):
            return 2
    stopped = [False]
    def cancel(_signum, _frame):
        stopped[0] = True
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    result = execute(actual, parent_pid=args.parent_pid, timeout=args.timeout, cancelled=lambda: stopped[0])
    result['effect_saved'] = None
    if effect is not None:
        try:
            publish_effect(args.root_fd, effect, result)
            result['effect_saved'] = True
        except (OSError, ValueError):
            result['effect_saved'] = False
    try:
        print(json.dumps(result, separators=(',', ':'), allow_nan=False), flush=True)
    except BrokenPipeError:
        pass  # Caller died; child collection already completed while holding the lease.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

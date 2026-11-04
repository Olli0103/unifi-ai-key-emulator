"""Shared private host authority and bounded writer lock, outside guest mounts.

The existing Apple reconciliation writer lives here so upgrade/supervisor
operations can use the same contract without cyclic actor imports.
"""
from contextlib import contextmanager
from contextlib import ExitStack
import os
import json
from pathlib import Path
import re
import stat

from .file_lock import acquire

_NAME = re.compile('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\Z')


class ControlStoreError(ValueError):
    pass


def container_listing(run, command='/usr/local/bin/container'):
    """A complete successful local observation, never failed output as absence."""
    from .protocol import _unique_object, _reject_constant, _finite_float
    try:
        code, output = run([command, 'list', '--all', '--format', 'json'])
        if type(code) is not int or code != 0 or not isinstance(output, str) or len(output) > 1024 * 1024:
            return None
        rows = json.loads(output, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
        if (not isinstance(rows, list) or len(rows) > 128
                or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                       or not _NAME.fullmatch(row['id']) for row in rows)
                or len({row['id'] for row in rows}) != len(rows)):
            return None
        return {row['id']: row for row in rows}
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def canonical_path(value, error=ControlStoreError):
    path = Path(value)
    if (not path.is_absolute() or path.resolve() != path or path.is_symlink()
            or any(char in str(path) for char in ':\r\n\x00')):
        raise error('apple_runtime_path_not_canonical')
    return path


def private_directory(value, error=ControlStoreError):
    path = canonical_path(value, error)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise error('apple_state_not_private')
    return path


@contextmanager
def configuration_writer(config):
    """Same two existing Key/Port mutex paths; retain a durable reservation too."""
    from .private_files import read_private
    config = canonical_path(config)
    private_directory(config.parent)
    read_private(config, 1024 * 1024)
    with ExitStack() as stack:
        for suffix in ('.admin.lock', '.lock'):
            path = config.with_name('.' + config.name + suffix)
            handle = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            stack.callback(os.close, handle)
            info = os.fstat(handle)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ControlStoreError('apple_configuration_lock_not_private')
            acquire(handle, timeout=5)
        from .device_workflow_fence import check
        check(config)
        yield config


@contextmanager
def writer(directory, *, mutate=True, error=ControlStoreError, validate=private_directory):
    directory = validate(directory)
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        if mutate:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                lock = os.open('.writer.lock', flags | os.O_CREAT, 0o600, dir_fd=root)
            except FileNotFoundError:
                # Existing Darwin O_CREAT race fallback; never recreate after
                # the first attempt or ignore ownership/type/link checks.
                lock = os.open('.writer.lock', flags, dir_fd=root)
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise error('apple_writer_lock_not_private')
            acquire(lock, timeout=5)

        def bound():
            found, opened = directory.lstat(), os.fstat(root)
            if (not stat.S_ISDIR(found.st_mode) or found.st_uid != os.geteuid() or found.st_mode & 0o077
                    or (found.st_dev, found.st_ino) != (opened.st_dev, opened.st_ino)):
                raise error('apple_writer_root_changed')
        bound.lease_fd = lock
        bound.root_fd = root
        bound()
        yield directory, bound
    finally:
        if lock is not None:
            os.close(lock)
        os.close(root)

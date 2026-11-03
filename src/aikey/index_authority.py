"""Shared runtime/migration exclusion on the exact state-directory lock inode.

Only a participating runtime publishes the marker. A migration refuses legacy
state without that marker; a failed health request is never stop evidence.
The lock is never removed. Run both sides on a filesystem/namespace whose flock
semantics are qualified; a changed device/inode or replaced root refuses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import secrets

from .file_lock import acquire

LOCK = '.search-index.lock'
MARKER = 'search-index-authority.json'


class IndexAuthorityError(RuntimeError):
    """A runtime or migration cannot establish exclusive index authority."""


class IndexAuthority:
    def __init__(self, state_dir: Path, *, runtime=False):
        self.path = Path(state_dir)
        self.directory = self.descriptor = None
        try:
            self.directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            root = os.fstat(self.directory)
            if root.st_uid != os.geteuid() or root.st_mode & 0o022:
                raise IndexAuthorityError('The index authority directory is not owner-controlled')
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if runtime:
                flags |= os.O_CREAT
            self.descriptor = os.open(LOCK, flags, 0o600, dir_fd=self.directory)
            meta = os.fstat(self.descriptor)
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                    or meta.st_mode & 0o077 or meta.st_nlink != 1):
                raise IndexAuthorityError('The index authority lock is not private and stable')
            acquire(self.descriptor, timeout=0)
            self.identity = (meta.st_dev, meta.st_ino)
            self.root_identity = (root.st_dev, root.st_ino)
            expected = {'schema': 'aikey-search-index-authority/1',
                        'lock_device': meta.st_dev, 'lock_inode': meta.st_ino}
            if runtime:
                try:
                    os.stat(MARKER, dir_fd=self.directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    self._marker()  # Refuse damaged/nonprivate state; do not repair it silently.
                self._publish(expected)
            elif self._marker() != expected:
                raise IndexAuthorityError('The state has no matching participating runtime authority')
            self.check()
        except (OSError, ValueError, TimeoutError, IndexAuthorityError) as exc:
            self.close()
            if isinstance(exc, IndexAuthorityError):
                raise
            if isinstance(exc, TimeoutError):
                raise IndexAuthorityError('The Key runtime or another migration holds index authority') from None
            raise IndexAuthorityError('Index authority is unavailable; do not infer that the Key is stopped') from None

    def _publish(self, value):
        temporary = '.index-authority-'+secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write((json.dumps(value, sort_keys=True)+'\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, MARKER, src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def _marker(self):
        descriptor = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        with os.fdopen(descriptor, 'rb') as stream:
            meta = os.fstat(stream.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid()
                    or meta.st_mode & 0o077 or meta.st_nlink != 1 or not 0 < meta.st_size <= 1024):
                raise IndexAuthorityError('The runtime participation marker is not a bounded private file')
            raw = stream.read(1025)
        from .camera_inventory import _json, InventoryError
        try:
            return _json(raw)
        except (ValueError, InventoryError):
            raise IndexAuthorityError('The runtime participation marker is invalid') from None

    def check(self):
        try:
            if self.descriptor is None or self.directory is None:
                raise IndexAuthorityError('Index authority is closed')
            root = self.path.stat(follow_symlinks=False)
            lock = os.stat(LOCK, dir_fd=self.directory, follow_symlinks=False)
            if ((root.st_dev, root.st_ino) != self.root_identity or not stat.S_ISDIR(root.st_mode)
                    or (lock.st_dev, lock.st_ino) != self.identity or not stat.S_ISREG(lock.st_mode)
                    or lock.st_uid != os.geteuid() or lock.st_mode & 0o077 or lock.st_nlink != 1
                    or self._marker() != {'schema': 'aikey-search-index-authority/1',
                        'lock_device': self.identity[0], 'lock_inode': self.identity[1]}):
                raise IndexAuthorityError('The index authority binding changed')
            return True
        except (OSError, ValueError):
            raise IndexAuthorityError('The index authority binding is unavailable') from None

    def close(self):
        for attr in ('descriptor', 'directory'):
            descriptor = getattr(self, attr, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attr, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

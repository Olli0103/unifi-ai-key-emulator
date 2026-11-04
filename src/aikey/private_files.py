"""Existing descriptor-bound private-byte read, shared with frozen host tools."""
import os
import stat


def read_private(path, limit, error=ValueError):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or not 1 <= info.st_size <= limit):
            raise error('device_file_not_private')
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise error('device_file_too_large')
    return raw

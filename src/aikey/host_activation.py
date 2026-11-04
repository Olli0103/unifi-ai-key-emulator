"""Cross-process saved-config reservation for a possibly late host startup.

Prepared reservations fence ALL ordinary config writers after the host mutex
is released. Startup can read only the exact reviewed bytes. No credential,
path, timestamp/foreign monotonic clock or executable authority is carried.
"""
import os
import json
from pathlib import Path
import re
import stat

from .config import atomic_private
from .protocol import _json_object

SCHEMA='aikey-host-activation/1'
IMAGE_LABEL='org.local-aikey.host-activation'
_HASH=re.compile('[0-9a-f]{64}\\Z')
_OP=re.compile('[0-9a-f]{32}\\Z')


class HostActivationError(ValueError):
    pass


def path(config):
    config=Path(config)
    return config.with_name('.'+config.name+'.host-activation.json')


def checked(value):
    fields={'schema','operation','authority','revision','configuration_revision','phase'}
    if (not isinstance(value,dict) or set(value)!=fields or value['schema']!=SCHEMA
            or not isinstance(value['operation'],str) or not _OP.fullmatch(value['operation'])
            or any(not isinstance(value[key],str) or not _HASH.fullmatch(value[key])
                   for key in ('authority','revision','configuration_revision'))
            or value['phase'] not in ('prepared','settled')):
        raise HostActivationError('host_activation_reservation_unavailable')
    return value


def read(config):
    try:
        descriptor=os.open(path(config),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise HostActivationError('host_activation_reservation_unavailable') from None
    try:
        with os.fdopen(descriptor,'rb') as source:
            info=os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                    or info.st_mode&0o077 or info.st_nlink!=1 or not 1<=info.st_size<=4096):
                raise ValueError
            value=_json_object(source.read(4097))
        return checked(value)
    except (OSError,ValueError,TypeError,RuntimeError):
        raise HostActivationError('host_activation_reservation_unavailable') from None


def check_writable(config):
    value=read(config)
    if value is not None and value['phase']=='prepared':
        raise HostActivationError('host_activation_needs_original_host_reconciliation')


def check_startup(config,revision):
    value=read(config)
    if value is not None and value['phase']=='prepared' and value['configuration_revision']!=revision:
        raise HostActivationError('host_activation_startup_bytes_not_reviewed')


def publish(config,value):
    """Caller holds the existing configuration mutex and its host authority."""
    checked(value)
    previous=read(config)
    if previous is not None and previous['phase']=='prepared':
        before={key:item for key,item in previous.items() if key!='phase'}
        after={key:item for key,item in value.items() if key!='phase'}
        if before!=after:
            raise HostActivationError('host_activation_owned_by_another_intent')
    atomic_private(path(config),(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
    descriptor=os.open(Path(config).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if read(config)!=value:
        raise HostActivationError('host_activation_publication_uncertain')

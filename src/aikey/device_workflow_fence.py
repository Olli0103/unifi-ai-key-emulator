"""Small shared configuration-intent fence; no controller/model/secret IO."""
import os
from pathlib import Path
import re
import stat

from .protocol import _json_object

SCHEMA='aikey-device-workflow/1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_PROFILE=re.compile(r'aikey|aiport(?::[a-z][a-z0-9_-]{0,31})?\Z')
FIELDS={'schema','profile','operation','before','after','identity','state'}


class DeviceIntentError(ValueError):
    pass


def path(config_path):
    config_path=Path(config_path)
    return config_path.with_name('.'+config_path.name+'.device-workflow.json')


def _record(value,*,previous=False):
    expected=FIELDS if previous else FIELDS|{'previous'}
    if (not isinstance(value,dict) or set(value)!=expected or value['schema']!=SCHEMA
            or not isinstance(value['profile'],str) or not _PROFILE.fullmatch(value['profile'])
            or value['operation'] not in ('controller','controller_rollback','camera_pool','camera_pool_rollback',
                                        'onvif_catalog','onvif_catalog_rollback','key_services','key_services_rollback',
                                        'port_features','port_features_rollback')
            or any(not isinstance(value[k],str) or not _SHA.fullmatch(value[k]) for k in ('before','after'))
            or value['state'] not in ('prepared','saved','rolled_back','abandoned')
            or previous and value['state']!='saved'
            or value['state']=='saved' and value['operation'] not in ('controller','camera_pool','onvif_catalog','key_services','port_features')
            or value['state']=='rolled_back' and value['operation'] not in ('controller_rollback','camera_pool_rollback','onvif_catalog_rollback','key_services_rollback','port_features_rollback')
            or not isinstance(value['identity'],list) or len(value['identity'])!=3):
        raise DeviceIntentError('invalid_device_transaction')
    for row,name in zip(value['identity'],('device.crt','device.key','identity.json'),strict=True):
        if (not isinstance(row,list) or len(row)!=2 or row[0]!=name
                or row[1] is None and name!='identity.json'
                or row[1] is not None and (not isinstance(row[1],str) or not _SHA.fullmatch(row[1]))):
            raise DeviceIntentError('invalid_device_transaction')
    if not previous and value['previous'] is not None:
        _record(value['previous'],previous=True)
        if value['previous']['profile']!=value['profile']:
            raise DeviceIntentError('invalid_device_transaction')
    return value


def read(config_path,*,durable=False):
    journal=path(config_path)
    descriptor=-1
    try:
        try:
            descriptor=os.open(journal,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor,'rb') as source:
            descriptor=-1
            info=os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                    or info.st_mode&0o077 or not 1<=info.st_size<=16384):
                raise DeviceIntentError('invalid_device_transaction')
            raw=source.read(16385)
            if len(raw)>16384:
                raise DeviceIntentError('invalid_device_transaction')
            value=_record(_json_object(raw))
            if durable:
                os.fsync(source.fileno())
        if durable:
            directory=os.open(journal.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return value
    except (OSError,ValueError,TypeError,RecursionError):
        raise DeviceIntentError('device_transaction_unavailable') from None
    finally:
        if descriptor>=0:
            os.close(descriptor)


def check(config_path,*,durable=True):
    record=read(config_path,durable=durable)
    if record is not None and record['state']=='prepared':
        raise DeviceIntentError('device_configuration_reconciliation_required')

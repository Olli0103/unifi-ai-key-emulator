"""Private ONVIF configuration catalog, explicitly NOT an ingest/PTZ service.

The catalog consumer is the admin inventory. No discovery, DNS, credential
exchange, camera identity/adoption or network request occurs in this module.
Credentials are independent immutable profile-owned references, never inline.
"""
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re

from .protocol import _json_object

SCHEMA='aikey-onvif-catalog/1'
_REF=re.compile(r'onvif-catalog\.([0-9a-f]{64})\.json\Z')
_ID=re.compile(r'[0-9a-f]{32}\Z')
_SECRET=re.compile(r'onvif-(username|password)-[0-9a-f]{32}\Z')
_ENTRY={'id','name','host','port','tls','certificate_pin','username_ref','password_ref'}


def valid_reference(value):
    return isinstance(value,str) and _REF.fullmatch(value) is not None


def _encode(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()


def _validate(value,mac):
    from .device_workflow import _host
    if (not isinstance(value,dict) or set(value)!={'schema','device_mac','entries'}
            or value['schema']!=SCHEMA or value['device_mac']!=mac
            or not isinstance(value['entries'],list) or len(value['entries'])>8):
        raise ValueError('invalid_onvif_catalog')
    ids,names=set(),set()
    for row in value['entries']:
        if (not isinstance(row,dict) or set(row)!=_ENTRY or not isinstance(row['id'],str) or not _ID.fullmatch(row['id'])
                or row['id'] in ids or not isinstance(row['name'],str) or not 1<=len(row['name'])<=128
                or row['name'].strip()!=row['name'] or any(ord(c)<32 or ord(c)==127 for c in row['name'])
                or row['name'].casefold() in names or not _host(row['host'])
                or type(row['port']) is not int or not 1<=row['port']<=65535 or type(row['tls']) is not bool
                or (not isinstance(row['certificate_pin'],str) or not re.fullmatch(r'[0-9a-f]{64}',row['certificate_pin'])
                    if row['tls'] else row['certificate_pin'] is not None)):
            raise ValueError('invalid_onvif_camera_metadata')
        for kind in ('username','password'):
            reference=row[kind+'_ref']
            if not isinstance(reference,str) or not _SECRET.fullmatch(reference) or not reference.startswith('onvif-'+kind+'-'):
                raise ValueError('invalid_onvif_credential_reference')
        ids.add(row['id'])
        names.add(row['name'].casefold())
    return value


def load(directory,reference,mac,*,verify_credentials=True):
    from .device_workflow import _private
    if reference is None:
        return {'schema':SCHEMA,'device_mac':mac,'entries':[]}
    if not valid_reference(reference):
        raise ValueError('invalid_onvif_catalog_reference')
    raw=_private(Path(directory)/reference,8192)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(),_REF.fullmatch(reference)[1]):
        raise ValueError('onvif_catalog_digest_mismatch')
    catalog=_validate(_json_object(raw),mac)
    if verify_credentials:
        for row in catalog['entries']:
            for kind in ('username','password'):
                raw=_private(Path(directory)/row[kind+'_ref'],4096)
                if not raw.strip():
                    raise ValueError('onvif_credential_unavailable')
    return catalog


def prepare(catalog,mac,selection):
    """Return reviewed metadata/resources; a caller publishes after admission."""
    _validate(catalog,mac)
    base={'operation','id'}
    fields={'name','host','port','tls','certificate_pin','username','password','credential_slot'}
    if (not isinstance(selection,dict) or not base<=set(selection)
            or selection['operation'] not in ('save','remove','clear') or not isinstance(selection['id'],str)
            or not _ID.fullmatch(selection['id'])):
        raise ValueError('invalid_onvif_edit')
    result=deepcopy(catalog)
    existing=next((row for row in result['entries'] if row['id']==selection['id']),None)
    resources=[]
    if selection['operation']=='clear':
        if set(selection)!=base or selection['id']!='0'*32:
            raise ValueError('invalid_onvif_clear')
        result['entries']=[]
    elif selection['operation']=='remove':
        if set(selection)!=base or existing is None:
            raise ValueError('unknown_onvif_camera')
        result['entries']=[row for row in result['entries'] if row['id']!=selection['id']]
    else:
        if set(selection)!=base|fields:
            raise ValueError('invalid_onvif_edit')
        row={name:selection[name] for name in ('id','name','host','port','tls','certificate_pin')}
        username,password=selection['username'],selection['password']
        if not isinstance(username,str) or not isinstance(password,str):
            raise ValueError('invalid_onvif_credentials')
        if username or password:
            slot=selection['credential_slot']
            if (not 1<=len(username.encode())<=512 or not 1<=len(password.encode())<=2048
                    or any(ord(c)<32 or ord(c)==127 for c in username+password)
                    or username.strip()!=username or password.strip()!=password
                    or not isinstance(slot,str) or not _ID.fullmatch(slot)):
                raise ValueError('invalid_onvif_credentials')
            for kind,value in (('username',username),('password',password)):
                reference='onvif-'+kind+'-'+slot
                row[kind+'_ref']=reference
                resources.append((reference,(value+'\n').encode()))
        else:
            if (existing is None or selection['credential_slot'] is not None
                    or any(existing[k]!=row[k] for k in ('host','port','tls','certificate_pin'))):
                raise ValueError('onvif_destination_change_requires_new_credentials')
            row.update({kind+'_ref':existing[kind+'_ref'] for kind in ('username','password')})
        result['entries']=[value for value in result['entries'] if value['id']!=row['id']]+[row]
        result['entries'].sort(key=lambda value:value['id'])
    _validate(result,mac)
    raw=_encode(result)
    if len(raw)>8192:
        raise ValueError('onvif_catalog_too_large')
    reference='onvif-catalog.'+hashlib.sha256(raw).hexdigest()+'.json'
    resources.append((reference,raw))
    return reference,tuple(resources),result


def public(catalog):
    return {'schema':SCHEMA,'configured_cameras':[{
        **{key:row[key] for key in ('id','name','host','port','tls','certificate_pin')},
        'credential_references_configured':True,'source':'local_registration_not_native_camera_identity',
        'ingest':'not_implemented','ptz':'not_implemented','recording':'not_implemented',
        'network_tested':False,'processing_enabled':False} for row in catalog['entries']],
        'native_adoption_changed':False,'network_requests':0}

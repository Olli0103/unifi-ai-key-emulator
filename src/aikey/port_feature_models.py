"""Host-published local model references, never HTTP-selected filesystem paths.

Publication hashes existing owned files; it does not load ONNX, download weights
or assert license/format/quality/native compatibility. The private catalog maps
those bytes to explicit paths in the eventual processor namespace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .aiport_faces import EMBEDDER_INPUTS
from .protocol import _json_object

SCHEMA='aikey-port-feature-models/1'
_SPACE=re.compile(r'port-face-space\.([0-9a-f]{64})\.json\Z')
FIELDS={'face':{'detector_path','detector_sha256','embedder_path','embedder_sha256','embedder_input'},
        'sound':{'model_path','model_sha256','class_map_path','class_map_sha256'}}


def checked(value):
    if (not isinstance(value,dict) or set(value)!={'schema','name','kind','settings','publication_check'}
            or value['schema']!=SCHEMA or not isinstance(value['kind'],str) or value['kind'] not in FIELDS
            or not isinstance(value['name'],str) or not 1<=len(value['name'])<=80
            or value['name'].strip()!=value['name'] or any(ord(c)<32 or ord(c)==127 for c in value['name'])
            or value['publication_check'] not in ('owned_file_sha256_only','saved_configuration_only')
            or not isinstance(value['settings'],dict) or set(value['settings'])!=FIELDS[value['kind']]):
        raise ValueError('invalid_port_model_binding')
    from .aiport_candidate import _offline_decoder_path
    for field,setting in value['settings'].items():
        if field.endswith('_path'):
            _offline_decoder_path(setting)
        elif field.endswith('_sha256'):
            if not isinstance(setting,str) or not re.fullmatch(r'[0-9a-f]{64}',setting):
                raise ValueError('invalid_port_model_binding')
        elif setting not in EMBEDDER_INPUTS:
            raise ValueError('invalid_port_model_binding')
    return value


def encode(value):
    return (json.dumps(checked(value),sort_keys=True,separators=(',',':'))+'\n').encode()


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


def load(path):
    from .device_workflow import _private
    return checked(_json_object(_private(Path(path),8192)))


def valid_space_ref(value):
    return isinstance(value,str) and _SPACE.fullmatch(value) is not None


def face_space(settings,mac):
    # Paths can move without changing weights. The pinned detector, encoder,
    # input preprocessing and fixed pipeline define the vector space instead.
    value={'schema':'aikey-port-face-space/1','mac':mac,'pipeline':'port-face-512/alignment-v1',
        'detector_sha256':settings['detector_sha256'],'embedder_sha256':settings['embedder_sha256'],
        'embedder_input':settings.get('embedder_input','arcface')}
    if (any(not isinstance(value[key],str) or not re.fullmatch(r'[0-9a-f]{64}',value[key])
            for key in ('detector_sha256','embedder_sha256')) or value['embedder_input'] not in EMBEDDER_INPUTS):
        raise ValueError('invalid_port_face_space')
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    return 'port-face-space.'+hashlib.sha256(raw).hexdigest()+'.json',raw


def load_face_space(directory,reference,mac,face=None):
    from .device_workflow import _private
    if not valid_space_ref(reference):
        raise ValueError('invalid_port_face_space_reference')
    raw=_private(Path(directory)/reference,8192)
    value=_json_object(raw)
    fields={'schema','mac','pipeline','detector_sha256','embedder_sha256','embedder_input'}
    if (set(value)!=fields or value['schema']!='aikey-port-face-space/1' or value['mac']!=mac
            or value['pipeline']!='port-face-512/alignment-v1'
            or face_space(value,mac)!=(reference,raw)
            or face is not None and face_space(face,mac)[0]!=reference):
        raise ValueError('port_face_model_space_migration_required')
    return value


def current_bindings(config):
    result=[]
    for kind,field in (('face','live_face'),('sound','live_sound')):
        current=config.get(field)
        if current:
            settings={name:current[name] for name in FIELDS[kind] if name in current}
            if kind=='face':
                settings.setdefault('embedder_input','arcface')
            result.append(checked({'schema':SCHEMA,'name':'Current saved '+kind+' model set',
                'kind':kind,'settings':settings,'publication_check':'saved_configuration_only'}))
    return result


def _hash_file(path,deadline):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as source:
        info=os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022
                or not 0<info.st_size<=1024*1024*1024):
            raise ValueError('invalid_model_source_file')
        digest=hashlib.sha256()
        count=0
        while block:=source.read(1024*1024):
            count+=len(block)
            if count>1024*1024*1024 or time.monotonic()>=deadline:
                raise ValueError('model_publication_budget_exceeded')
            digest.update(block)
        final=os.fstat(source.fileno())
        named=os.stat(path,follow_symlinks=False)
        if (count!=info.st_size or final.st_mtime_ns!=info.st_mtime_ns
                or (named.st_dev,named.st_ino)!=(info.st_dev,info.st_ino)):
            raise ValueError('model_source_changed')
        return digest.hexdigest()


def publish(output,*,kind,name,files,embedder_input='arcface'):
    expected={'detector','embedder'} if kind=='face' else {'model','class_map'} if kind=='sound' else set()
    if not expected or not isinstance(files,dict) or set(files)!=expected:
        raise ValueError('invalid_model_publication')
    deadline=time.monotonic()+30
    settings={}
    for field,(host_path,runtime_path) in files.items():
        settings[field+'_path']=runtime_path
        settings[field+'_sha256']=_hash_file(host_path,deadline)
    if kind=='face':
        settings['embedder_input']=embedder_input
    value=checked({'schema':SCHEMA,'name':name,'kind':kind,'settings':settings,
                   'publication_check':'owned_file_sha256_only'})
    output=Path(output)
    parent=output.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or parent.st_mode&0o077:
        raise ValueError('model_binding_parent_not_private')
    # Host publication is create-only, not a web action or an in-place retarget.
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as destination:
        destination.write(encode(value))
        destination.flush()
        os.fsync(destination.fileno())
    descriptor=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if load(output)!=value:
        raise ValueError('model_binding_publication_readback_failed')
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=tuple(FIELDS),required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--embedder-input',choices=EMBEDDER_INPUTS,default='arcface')
    for field in ('detector','embedder','model','class-map'):
        parser.add_argument('--'+field+'-host',type=Path)
        parser.add_argument('--'+field+'-runtime')
    args=parser.parse_args(argv)
    try:
        fields=('detector','embedder') if args.kind=='face' else ('model','class_map')
        if any(getattr(args,field+'_'+suffix) is not None for field in set(('detector','embedder','model','class_map'))-set(fields)
               for suffix in ('host','runtime')):
            raise ValueError
        files={field:(getattr(args,field+'_host'),getattr(args,field+'_runtime')) for field in fields}
        if any(host is None or runtime is None for host,runtime in files.values()):
            raise ValueError
        value=publish(args.output,kind=args.kind,name=args.name,files=files,embedder_input=args.embedder_input)
        print(json.dumps({'published':True,'kind':args.kind,'binding_fingerprint':fingerprint(value),
                          'models_loaded':False,'network_requests':0,'native_compatibility':'not_tested'}))
        return 0
    except (OSError,ValueError,TypeError):
        print(json.dumps({'error':'port_model_binding_publication_refused','automatic_retry':False}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())

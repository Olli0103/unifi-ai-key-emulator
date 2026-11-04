"""Reviewed, configuration-only device transactions, separate from providers.

The web adapter owns session/one-use confirmation and explicit consent. This
store never opens a controller connection, changes an adopted identity, starts
a processor or claims saved bytes are the live runtime. Trust objects are
durably published before the one-file configuration pointer switch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass,field
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import stat

from .aiport_config_store import AiPortConfigurationStore
from .config import atomic_private
from .config_store import ConfigurationStore
from .controller_trust import DIRECTORY,TrustObject,install,prepare,selected
from . import device_workflow_fence as fence
from .protocol import _json_object

SCHEMA='aikey-device-workflow/1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_PROFILE=re.compile(r'aikey|aiport(?::[a-z][a-z0-9_-]{0,31})?\Z')
_LABEL=re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z')
_KEY_CONTROLLER={'host','expected_fingerprint','trust_ref','verify_hostname',
                 'search_trust_ref','search_expected_fingerprint'}
_PORT_CONTROLLER={'controller_ip','controller_pin','controller_trust_ref'}


class DeviceWorkflowError(ValueError):
    """Fixed safe codes only."""


class DeviceRevisionConflict(DeviceWorkflowError):
    pass


class DeviceOutcomeUncertain(DeviceWorkflowError):
    pass


def _encode(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _host(value):
    if not isinstance(value,str) or not 1<=len(value)<=253:
        return False
    if re.fullmatch(r'[0-9.]+',value):
        try:
            ipaddress.IPv4Address(value)
            return True
        except ValueError:
            return False
    return all(_LABEL.fullmatch(label) for label in value.split('.'))


def _private(path,limit):
    from .private_files import read_private
    return read_private(path, limit, DeviceWorkflowError)


@dataclass(frozen=True)
class DevicePreview:
    profile: str
    operation: str
    current_revision: str
    resulting_revision: str
    changed_fields: tuple[str,...]
    required_consents: tuple[str,...]
    destination: str
    fingerprints: tuple[str,...]
    _selection: bytes=field(repr=False)
    _encoded: bytes=field(repr=False)
    _identity: tuple=field(repr=False)
    _trust: tuple[TrustObject,...]=field(repr=False)
    _secrets: tuple=field(default=(),repr=False)
    _channels: tuple=field(default=(),repr=False)

    def public(self):
        return {'schema':SCHEMA,'profile':self.profile,'operation':self.operation,
            'current_revision':self.current_revision,'resulting_revision':self.resulting_revision,
            'changed_fields':list(self.changed_fields),'required_consents':list(self.required_consents),
            'controller_destination':self.destination,'controller_fingerprints':list(self.fingerprints),
            'controller_channel_pins':[{'channel':channel,'fingerprint':pin}
                for channel,pin in zip(self._channels,self.fingerprints,strict=True)] if self._channels else [],
            'identity_effect':'existing_identity_files_unchanged',
            'activation':'saved_not_active_runtime','restart_required':bool(self.changed_fields)}


class DeviceWorkflowStore:
    def __init__(self,profile,store,identity_directory):
        if (not isinstance(profile,str) or not _PROFILE.fullmatch(profile)
                or profile=='aikey' and not isinstance(store,ConfigurationStore)
                or profile!='aikey' and not isinstance(store,AiPortConfigurationStore)):
            raise DeviceWorkflowError('invalid_device_profile')
        self.profile,self.store=profile,store
        self.kind='aikey' if profile=='aikey' else 'aiport'
        self.identity_directory=Path(identity_directory).absolute()
        self.journal=fence.path(self.store.path)

    def _identity(self,current):
        from .aiport_ingest import normalize_mac
        info=self.identity_directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077):
            raise DeviceWorkflowError('device_identity_directory_not_private')
        mac=normalize_mac(current['device']['mac'] if self.kind=='aikey' else current['mac'])
        guard=[]
        for name in ('device.crt','device.key','identity.json'):
            path=self.identity_directory/name
            try:
                raw=_private(path,32768)
            except FileNotFoundError:
                if name!='identity.json':
                    raise DeviceWorkflowError('device_identity_incomplete') from None
                guard.append((name,None))
                continue
            if name=='identity.json' and normalize_mac(_json_object(raw)['mac'])!=mac:
                raise DeviceWorkflowError('device_identity_mismatch')
            guard.append((name,_sha(raw)))
        return tuple(guard)

    def _read(self):
        content=_private(self.store.path,1024*1024 if self.kind=='aikey' else 4096)
        raw=_json_object(content)
        self._validate(raw)
        return content,raw

    def _validate(self,candidate):
        # Existing complete validators stay authoritative. No provider patch
        # allowlist, identity guard or index-migration guard is weakened.
        self.store._validate(deepcopy(candidate))

    def _assert_controller_only(self,before,after):
        old,new=deepcopy(before),deepcopy(after)
        if self.kind=='aikey':
            for value in (old,new):
                controller=value.get('controller')
                if not isinstance(controller,dict):
                    raise DeviceWorkflowError('invalid_controller_settings')
                for name in _KEY_CONTROLLER:
                    controller.pop(name,None)
        else:
            for value in (old,new):
                for name in _PORT_CONTROLLER:
                    value.pop(name,None)
        if old!=new:
            raise DeviceWorkflowError('device_identity_or_unrelated_scope_change')

    def _prepare_controller(self,expected,selection,content,current):
        if not isinstance(expected,str) or not _SHA.fullmatch(expected) or not hmac.compare_digest(_sha(content),expected):
            raise DeviceRevisionConflict('device_configuration_changed')
        fields={'host','certificate_pem','fingerprint'}
        allowed=fields|({'verify_hostname','search_certificate_pem','search_fingerprint'} if self.kind=='aikey' else set())
        if (not isinstance(selection,dict) or not fields<=set(selection)<=allowed
                or not _host(selection['host'])
                or self.kind=='aikey' and type(selection.get('verify_hostname',True)) is not bool
                or ('search_certificate_pem' in selection)!=('search_fingerprint' in selection)):
            raise DeviceWorkflowError('invalid_controller_settings')
        mac=current['device']['mac'] if self.kind=='aikey' else current['mac']
        from .aiport_ingest import normalize_mac
        trust=prepare(host=selection['host'],device_mac=normalize_mac(mac),purpose='control',
                      certificate_pem=selection['certificate_pem'],fingerprint=selection['fingerprint'])
        objects=[trust]
        candidate=deepcopy(current)
        if self.kind=='aikey':
            controller=candidate['controller']
            controller.update(host=trust.host,expected_fingerprint=trust.fingerprint,trust_ref=trust.reference,
                              verify_hostname=selection.get('verify_hostname',True))
            if 'search_certificate_pem' in selection:
                search=prepare(host=trust.host,device_mac=trust.device_mac,purpose='search',
                    certificate_pem=selection['search_certificate_pem'],fingerprint=selection['search_fingerprint'])
                objects.append(search)
                controller.update(search_trust_ref=search.reference,search_expected_fingerprint=search.fingerprint)
            elif any(current['controller'].get(k) for k in ('search_ca_file','search_trust_ref')):
                # Retain the separately pinned public certificate, but bind it
                # to this profile and the newly reviewed destination. Both
                # channel pins are displayed by the confirmation adapter.
                try:
                    previous=selected(current,kind='aikey',state_dir=self.identity_directory,channel='search')
                    if previous is not None:
                        pem=previous.certificate_pem
                    else:
                        from .state_backup_preflight import _host as host_file
                        path=host_file(current['controller']['search_ca_file'],self.identity_directory,
                                   current['runtime']['state_dir'])
                        pem=_private(path,16384).decode('ascii')
                    search=prepare(host=trust.host,device_mac=trust.device_mac,purpose='search',
                        certificate_pem=pem,fingerprint=current['controller']['search_expected_fingerprint'])
                except (OSError,ValueError,TypeError,KeyError,UnicodeError):
                    raise DeviceWorkflowError('separate_search_trust_review_required') from None
                objects.append(search)
                controller.update(search_trust_ref=search.reference,search_expected_fingerprint=search.fingerprint)
        else:
            candidate.update(controller_ip=trust.host,controller_pin=trust.fingerprint,controller_trust_ref=trust.reference)
        self._assert_controller_only(current,candidate)
        self._validate(candidate)
        from .config_store import _changed
        changes=tuple(_changed(current,candidate))
        encoded=_encode(candidate) if changes else content
        if len(encoded)>(1024*1024 if self.kind=='aikey' else 4096):
            raise DeviceWorkflowError('device_configuration_too_large')
        consents=['controller_destination_and_pin_reviewed']
        if self.kind=='aikey' and not candidate['controller'].get('verify_hostname',True):
            consents.append('explicit_certificate_pin_instead_of_hostname')
        return DevicePreview(self.profile,'controller',expected,_sha(encoded),changes,tuple(consents),
            trust.host,tuple(t.fingerprint for t in objects),_encode(selection),encoded,
            self._identity(current),tuple(objects),_channels=tuple(item.purpose for item in objects))

    def preview_controller(self,expected,selection):
        content,current=self._read()
        return self._prepare_controller(expected,selection,content,current)

    def _prepare_pool(self,expected,selection,content,current):
        if self.kind!='aiport':
            raise DeviceWorkflowError('camera_pool_requires_aiport')
        if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected:
            raise DeviceRevisionConflict('device_configuration_changed')
        from .device_camera_pool import candidate
        if (not isinstance(selection,dict) or not {'cameras','decoder'}<=set(selection)
                <= {'cameras','decoder','detector','api_key','key_slot'}):
            raise DeviceWorkflowError('invalid_camera_pool_selection')
        secrets_to_publish=[]
        prepared=deepcopy(selection)
        if 'detector' in selection:
            detector=prepared['detector']
            if (not isinstance(detector,dict) or not {'provider_config','threshold','smart_types'}<=set(detector)
                    <= {'provider_config','threshold','smart_types','max_requests_per_hour'}
                    or not isinstance(detector['provider_config'],dict)
                    or not {'provider','model','base_url','allow_remote','allow_insecure_http','max_output_tokens'}
                    <= set(detector['provider_config'])
                    <= {'provider','model','base_url','allow_remote','allow_insecure_http','max_output_tokens'}):
                raise DeviceWorkflowError('invalid_initial_object_detector')
            provider=detector['provider_config']
            secret=prepared.pop('api_key',None)
            slot=prepared.pop('key_slot',None)
            if secret is not None:
                if (not isinstance(secret,str) or not 8<=len(secret)<=4096 or not secret.isascii()
                        or not secret.isprintable() or any(c.isspace() for c in secret)
                        or not isinstance(slot,str) or not re.fullmatch(r'[0-9a-f]{32}',slot)):
                    raise DeviceWorkflowError('invalid_initial_provider_credential')
                name='provider-key-'+slot
                provider['api_key_file']=str(self.store.runtime_state_dir/name)
                secrets_to_publish.append((name,(secret+'\n').encode()))
            elif slot is not None or provider.get('provider') in ('openai','anthropic'):
                raise DeviceWorkflowError('initial_provider_credential_required')
            detector['inference_backend']='vision_api'
        elif {'api_key','key_slot'} & set(selection):
            raise DeviceWorkflowError('camera_pool_does_not_replace_provider_credentials')
        candidate_config=candidate(current,prepared)
        self._validate(candidate_config)
        from .config_store import _changed
        # Do not expose per-camera MACs from dynamic mapping keys to a form.
        changes=tuple(sorted({key.split('.')[0] for key in _changed(current,candidate_config)}))
        encoded=_encode(candidate_config) if changes else content
        if len(encoded)>4096:
            raise DeviceWorkflowError('device_configuration_too_large')
        consents=['reviewed_camera_capture_destination_and_cost','native_pairing_is_separate']
        if candidate_config.get('live_pool_detector',{}).get('provider_config',{}).get('provider') in ('openai','anthropic'):
            consents.append('explicit_remote_camera_frames_and_cost')
        return DevicePreview(self.profile,'camera_pool',expected,_sha(encoded),changes,tuple(consents),
            current['controller_ip'],(current['controller_pin'],),_encode(selection),encoded,
            self._identity(current),(),tuple(secrets_to_publish))

    def _publish_secrets(self,items):
        for name,raw in items:
            target=self.store.path.parent/name
            try:
                descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            except FileExistsError:
                if not hmac.compare_digest(_private(target,8192),raw):
                    raise DeviceWorkflowError('initial_provider_credential_conflict')
            else:
                with os.fdopen(descriptor,'wb') as output:
                    os.fchmod(output.fileno(),0o600)
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
            if not hmac.compare_digest(_private(target,8192),raw):
                raise DeviceWorkflowError('initial_provider_credential_readback_failed')
            descriptor=os.open(target,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(descriptor,'rb') as source:
                info=os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077
                        or not 1<=info.st_size<=8192 or not hmac.compare_digest(source.read(8193),raw)):
                    raise DeviceWorkflowError('initial_provider_credential_readback_failed')
                os.fsync(source.fileno())
        if items:
            descriptor=os.open(self.store.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def preview_camera_pool(self,expected,selection):
        content,current=self._read()
        return self._prepare_pool(expected,selection,content,current)

    def _prepare_onvif(self,expected,selection,content,current):
        if self.kind!='aiport':
            raise DeviceWorkflowError('onvif_metadata_requires_aiport')
        if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected:
            raise DeviceRevisionConflict('device_configuration_changed')
        from .onvif_catalog import load,prepare
        from .aiport_ingest import normalize_mac
        mac=normalize_mac(current['mac'])
        catalog=load(self.identity_directory,current.get('onvif_catalog_ref'),mac,verify_credentials=False)
        reference,resources,_=prepare(catalog,mac,selection)
        candidate=deepcopy(current)
        candidate['onvif_catalog_ref']=reference
        self._validate(candidate)
        changes=('onvif_catalog_ref',) if reference!=current.get('onvif_catalog_ref') else ()
        encoded=_encode(candidate) if changes else content
        if len(encoded)>4096:
            raise DeviceWorkflowError('device_configuration_too_large')
        consents=['onvif_metadata_only_no_ingest','onvif_private_credential_references']
        if selection['operation']=='save' and not selection['tls']:
            consents.append('onvif_unencrypted_credentials_if_future_ingest')
        return DevicePreview(self.profile,'onvif_catalog',expected,_sha(encoded),changes,tuple(consents),
            current['controller_ip'],(current['controller_pin'],),_encode(selection),encoded,
            self._identity(current),(),resources)

    def preview_onvif(self,expected,selection):
        content,current=self._read()
        return self._prepare_onvif(expected,selection,content,current)

    def _prepare_key_service(self,expected,selection,content,current):
        if self.kind!='aikey':
            raise DeviceWorkflowError('local_key_service_requires_aikey')
        if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected:
            raise DeviceRevisionConflict('device_configuration_changed')
        from .key_service_config import candidate,assert_scope
        candidate_config=candidate(current,selection)
        assert_scope(current,candidate_config)
        self._validate(candidate_config)
        # Reuse all existing identity, search-encoder and credential-route
        # invariants. This typed flow cannot migrate an index or another role.
        self.store._assert_immutable(current,candidate_config)
        self.store._assert_search_profile(deepcopy(candidate_config),deepcopy(current))
        self.store._assert_credential_routes(current,candidate_config)
        from .config_store import _changed
        changes=tuple(sorted({key.split('.')[0] for key in _changed(current,candidate_config)}))
        encoded=_encode(candidate_config) if changes else content
        if len(encoded)>1024*1024:
            raise DeviceWorkflowError('device_configuration_too_large')
        consents=['disable_saved_local_feature'] if selection['mode']=='disable' else ['local_service_destination_and_role_contract']
        if selection['mode']=='configure':
            if selection['role']=='face_recognition':
                consents.append('face_recognition_named_camera_scope')
                if selection['native_face_cameras']=='process':
                    consents.append('native_face_camera_processing')
            else:
                consents.append('controller_requested_deep_or_enhancement_scope')
        return DevicePreview(self.profile,'key_services',expected,_sha(encoded),changes,tuple(consents),
            current['controller']['host'],(),_encode(selection),encoded,self._identity(current),())

    def preview_key_service(self,expected,selection):
        content,current=self._read()
        return self._prepare_key_service(expected,selection,content,current)

    def key_service_status(self):
        if self.kind!='aikey':
            raise DeviceWorkflowError('local_key_service_requires_aikey')
        from .key_service_config import ROLES
        content,current=self._read()
        return {'profile':self.profile,'revision':_sha(content),
            'roles':{role:deepcopy(current.get(role)) for role in ROLES},'activation':'saved_not_active_runtime'}

    def _prepare_port_feature(self,expected,selection,content,current):
        if self.kind!='aiport':
            raise DeviceWorkflowError('port_feature_requires_aiport')
        if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected:
            raise DeviceRevisionConflict('device_configuration_changed')
        from .port_feature_config import candidate,assert_scope
        candidate_config=candidate(current,selection)
        resources=()
        if selection['feature']=='face' and selection['mode']=='configure':
            from .port_feature_models import face_space,load_face_space
            from .aiport_ingest import normalize_mac
            mac=normalize_mac(current['mac'])
            reference,raw=face_space(candidate_config['live_face'],mac)
            if current.get('face_model_space_ref'):
                load_face_space(self.identity_directory,current['face_model_space_ref'],mac,current.get('live_face'))
                if reference!=current['face_model_space_ref']:
                    raise DeviceWorkflowError('port_face_model_space_migration_required')
            elif current.get('live_face') and face_space(current['live_face'],mac)[0]!=reference:
                raise DeviceWorkflowError('port_face_model_space_migration_required')
            candidate_config['face_model_space_ref']=reference
            resources=((reference,raw),)
        assert_scope(current,candidate_config)
        old,new=deepcopy(current),deepcopy(candidate_config)
        for value in (old,new):
            value.pop('face_model_space_ref',None)
        self.store._assert_policy_only(old,new)
        self.store._assert_role_routes(current,candidate_config)
        self._validate(candidate_config)
        from .config_store import _changed
        changes=tuple(sorted({key.split('.')[0] for key in _changed(current,candidate_config)}))
        encoded=_encode(candidate_config) if changes else content
        if len(encoded)>4096:
            raise DeviceWorkflowError('device_configuration_too_large')
        consents=(('disable_saved_port_feature',) if selection['mode']=='disable' else
                  ('port_named_feature_camera_scope','port_'+selection['feature']+'_processing'))
        return DevicePreview(self.profile,'port_features',expected,_sha(encoded),changes,consents,
            current['controller_ip'],(),_encode(selection),encoded,self._identity(current),(),resources)

    def preview_port_feature(self,expected,selection):
        content,current=self._read()
        return self._prepare_port_feature(expected,selection,content,current)

    def port_feature_status(self):
        if self.kind!='aiport':
            raise DeviceWorkflowError('port_feature_requires_aiport')
        from .port_feature_config import ROOTS
        content,current=self._read()
        return {'profile':self.profile,'revision':_sha(content),'configuration':current,
            'features':{feature:deepcopy(current.get('live_'+feature if feature!='speech' else 'live_speech_cameras'))
                        for feature in ROOTS},'activation':'saved_not_active_runtime'}

    def onvif_status(self):
        from .onvif_catalog import load,public
        from .aiport_ingest import normalize_mac
        content,current=self._read()
        if self.kind!='aiport':
            raise DeviceWorkflowError('onvif_metadata_requires_aiport')
        return dict(public(load(self.identity_directory,current.get('onvif_catalog_ref'),
            normalize_mac(current['mac']),verify_credentials=False)),profile=self.profile,revision=_sha(content))

    def camera_pool_status(self):
        content,current=self._read()
        if self.kind!='aiport':
            raise DeviceWorkflowError('camera_pool_requires_aiport')
        detector=current.get('live_pool_detector',{})
        return {'profile':self.profile,'revision':_sha(content),
            'cameras':[row['camera_mac'] for row in current.get('paired_streams',[])],
            'decoders':list(dict.fromkeys(row['ffmpeg_path'] for row in current.get('paired_streams',[]))),
            'detector_configured':bool(detector),'route':{key:detector.get('provider_config',{}).get(key)
                for key in ('provider','model','base_url')},
            'backend':detector.get('inference_backend','local') if detector else None,
            'source':'local_capture_allowlist_not_native_pairing',
            'activation':'saved_not_active_runtime'}

    def _assert_operation_scope(self,operation,before,after):
        if operation=='controller':
            self._assert_controller_only(before,after)
        elif operation=='camera_pool' and self.kind=='aiport':
            from .device_camera_pool import assert_scope
            assert_scope(before,after)
        elif operation=='onvif_catalog' and self.kind=='aiport':
            old,new=deepcopy(before),deepcopy(after)
            for value in (old,new):
                value.pop('onvif_catalog_ref',None)
            if old!=new:
                raise DeviceWorkflowError('onvif_unrelated_scope_change')
        elif operation=='key_services' and self.kind=='aikey':
            from .key_service_config import assert_scope
            assert_scope(before,after)
        elif operation=='port_features' and self.kind=='aiport':
            from .port_feature_config import assert_scope
            assert_scope(before,after)
        else:
            raise DeviceWorkflowError('unsupported_device_operation')

    def _verify_operation_resources(self,current,operation):
        self._verify_selected(current)
        if self.kind=='aiport' and current.get('onvif_catalog_ref'):
            from .onvif_catalog import load
            from .aiport_ingest import normalize_mac
            load(self.identity_directory,current['onvif_catalog_ref'],normalize_mac(current['mac']))
        if self.kind=='aiport' and current.get('face_model_space_ref'):
            from .port_feature_models import load_face_space
            from .aiport_ingest import normalize_mac
            load_face_space(self.identity_directory,current['face_model_space_ref'],normalize_mac(current['mac']),current.get('live_face'))
        if not operation.startswith('camera_pool'):
            return
        detector=current.get('live_pool_detector',{})
        for name in ('provider_config','ocr_provider_config'):
            reference=detector.get(name,{}).get('api_key_file')
            if reference is None:
                continue
            path=Path(reference)
            if path.parent not in (self.store.runtime_state_dir,self.store.path.parent):
                raise DeviceWorkflowError('camera_provider_key_not_profile_bound')
            _private(self.store.path.parent/path.name,8192)

    def probe_spec(self,preview):
        if (not isinstance(preview,DevicePreview) or preview.profile!=self.profile
                or preview.operation!='controller'):
            raise DeviceWorkflowError('invalid_device_probe_review')
        fresh=self.preview_controller(preview.current_revision,_json_object(preview._selection))
        if fresh!=preview:
            raise DeviceRevisionConflict('device_probe_review_changed')
        candidate=_json_object(fresh._encoded)
        ports=(candidate['controller'] if self.kind=='aikey' else {})
        return {'targets':[(trust,ports.get('search_port',7443) if trust.purpose=='search'
                           else ports.get('control_port',7442)) for trust in fresh._trust],
                'verify_hostname':ports.get('verify_hostname',True) if self.kind=='aikey' else False}

    def controller_status(self):
        content,current=self._read()
        controller=current['controller'] if self.kind=='aikey' else current
        return {'schema':SCHEMA,'profile':self.profile,'revision':_sha(content),
            'host':controller.get('host' if self.kind=='aikey' else 'controller_ip'),
            'verify_hostname':controller.get('verify_hostname',True) if self.kind=='aikey' else False,
            'separate_search_trust':self.kind=='aikey' and any(controller.get(k) for k in ('search_ca_file','search_trust_ref')),
            'activation':'saved_not_active_runtime','identity_guarded':bool(self._identity(current))}

    def _verify_selected(self,current):
        for channel in (('control','search') if self.kind=='aikey' else ('control',)):
            selected(current,kind=self.kind,state_dir=self.identity_directory,channel=channel)

    @staticmethod
    def _admit(admission):
        if admission is not None and (not callable(admission) or admission() is not True):
            raise DeviceRevisionConflict('device_review_authority_expired_or_replaced')

    def _settled(self):
        try:
            fence.check(self.store.path)
        except fence.DeviceIntentError:
            raise DeviceWorkflowError('device_configuration_reconciliation_required') from None

    def _archived(self,revision):
        if not isinstance(revision,str) or not _SHA.fullmatch(revision):
            raise DeviceWorkflowError('invalid_device_history')
        info=self.store.history_dir.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077):
            raise DeviceWorkflowError('invalid_device_history')
        raw=_private(self.store.history_dir/(revision+'.json'),1024*1024 if self.kind=='aikey' else 4096)
        if _sha(raw)!=revision:
            raise DeviceWorkflowError('invalid_device_history')
        checked=_json_object(raw)
        self._validate(checked)
        return raw,checked

    def apply(self,preview,*,acknowledgements,admission=None):
        if (not isinstance(preview,DevicePreview) or preview.profile!=self.profile
                or preview.operation not in ('controller','camera_pool','onvif_catalog','key_services','port_features')
                or not isinstance(acknowledgements,set) or acknowledgements!=set(preview.required_consents)):
            raise DeviceWorkflowError('device_review_confirmation_required')
        with self.store._locked(allow_device_pending=True):
            self._settled()
            prior=fence.read(self.store.path,durable=True)
            if prior is not None and prior['profile']!=self.profile:
                raise DeviceWorkflowError('device_transaction_profile_mismatch')
            content,current=self._read()
            prepare_operation={'controller':self._prepare_controller,'camera_pool':self._prepare_pool,
                               'onvif_catalog':self._prepare_onvif,'key_services':self._prepare_key_service,
                               'port_features':self._prepare_port_feature}[preview.operation]
            fresh=prepare_operation(preview.current_revision,_json_object(preview._selection),content,current)
            if fresh!=preview:
                raise DeviceRevisionConflict('device_review_changed')
            # The HTTP owner rechecks its monotonic one-use authority AFTER
            # lock waiting and complete validation, before the first write.
            self._admit(admission)
            self._publish_secrets(fresh._secrets)
            directory=self.identity_directory/DIRECTORY
            if fresh._trust:
                directory.mkdir(mode=0o700,exist_ok=True)
            for trust in fresh._trust:
                install(directory,trust)
            self._verify_operation_resources(_json_object(fresh._encoded),fresh.operation)
            # A newly appeared/changed identity is not covered by the review.
            if self._identity(current)!=preview._identity:
                raise DeviceRevisionConflict('device_identity_changed')
            if not fresh.changed_fields:
                self._verify_selected(current)
                return dict(fresh.public(),outcome='configuration_unchanged')
            self.store._archive(content)
            previous=({key:prior[key] for key in fence.FIELDS}
                      if prior is not None and prior['state']=='saved' else None)
            record={'schema':SCHEMA,'profile':self.profile,'operation':fresh.operation,
                    'before':fresh.current_revision,'after':fresh.resulting_revision,
                    'identity':[list(item) for item in fresh._identity],'state':'prepared','previous':previous}
            try:
                atomic_private(self.journal,_encode(record))
                atomic_private(self.store.path,fresh._encoded)
                persisted,checked=self._read()
                if _sha(persisted)!=fresh.resulting_revision or self._identity(checked)!=fresh._identity:
                    raise DeviceWorkflowError('device_readback_mismatch')
                self._verify_operation_resources(checked,fresh.operation)
                record['state']='saved'
                record['previous']=None
                atomic_private(self.journal,_encode(record))
            except (OSError,ValueError,RuntimeError):
                # The config or final receipt may already be published. Do not
                # retry or delete staged material after an uncertain write.
                raise DeviceOutcomeUncertain('device_save_outcome_uncertain') from None
            return dict(fresh.public(),outcome='saved_and_read_back')

    def _prepare_rollback(self,expected,content,current,record):
        if (not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected
                or record is None or record['profile']!=self.profile or record['state']!='saved'
                or record['after']!=expected
                or tuple(tuple(row) for row in record['identity'])!=self._identity(current)):
            raise DeviceRevisionConflict('device_rollback_not_current')
        target,checked=self._archived(record['before'])
        operation=record['operation']
        if operation=='port_features' and current.get('face_model_space_ref') and not checked.get('face_model_space_ref'):
            # Restoring a disabled feature must not erase the vector-space
            # guard while native face groups may still contain old vectors.
            checked=deepcopy(checked)
            checked['face_model_space_ref']=current['face_model_space_ref']
            self._validate(checked)
            target=_encode(checked)
            if _sha(target)==expected:
                raise DeviceRevisionConflict('face_model_space_guard_has_no_reversible_policy_delta')
        self._assert_operation_scope(operation,checked,current)
        self._verify_operation_resources(checked,operation)
        controller=checked['controller'] if self.kind=='aikey' else checked
        named_pins=[('control',controller.get('expected_fingerprint' if self.kind=='aikey' else 'controller_pin'))]
        if self.kind=='aikey' and controller.get('search_expected_fingerprint'):
            named_pins.append(('search',controller['search_expected_fingerprint']))
        named_pins=[(name,value.replace(':','').lower()) for name,value in named_pins if isinstance(value,str)]
        pins=tuple(value for _,value in named_pins)
        from .config_store import _changed
        consents=('restore_prior_saved_device_settings','controller_destination_and_pin_reviewed')
        if operation=='camera_pool':
            consents=('restore_prior_camera_capture_and_feature_permissions','native_pairing_is_separate')
            if checked.get('live_pool_detector',{}).get('provider_config',{}).get('provider') in ('openai','anthropic'):
                consents+=('explicit_remote_camera_frames_and_cost',)
        elif operation=='onvif_catalog':
            consents=('onvif_metadata_only_no_ingest','restore_onvif_saved_credentials')
        elif operation=='key_services':
            consents=('restore_saved_local_feature_scope','local_service_destination_and_role_contract')
        elif operation=='port_features':
            consents=('restore_saved_port_feature_scope','port_named_feature_camera_scope')
        changes=tuple(_changed(current,checked))
        if operation in ('camera_pool','key_services','port_features'):
            changes=tuple(sorted({key.split('.')[0] for key in changes}))
        return DevicePreview(self.profile,operation+'_rollback',expected,_sha(target),
            changes,consents,
            controller.get('host' if self.kind=='aikey' else 'controller_ip',''),pins,
            _encode({'record_sha256':_sha(_encode(record)),'target_revision':_sha(target)}),
            target,self._identity(current),(),_channels=tuple(name for name,_ in named_pins))

    def preview_rollback(self,expected):
        content,current=self._read()
        return self._prepare_rollback(expected,content,current,fence.read(self.store.path,durable=True))

    def rollback(self,preview,*,acknowledgements,admission=None):
        if (not isinstance(preview,DevicePreview) or preview.profile!=self.profile
                or preview.operation not in ('controller_rollback','camera_pool_rollback','onvif_catalog_rollback','key_services_rollback','port_features_rollback')
                or not isinstance(acknowledgements,set)
                or acknowledgements!=set(preview.required_consents)):
            raise DeviceWorkflowError('device_review_confirmation_required')
        with self.store._locked(allow_device_pending=True):
            self._settled()
            content,current=self._read()
            previous=fence.read(self.store.path,durable=True)
            fresh=self._prepare_rollback(preview.current_revision,content,current,previous)
            if fresh!=preview:
                raise DeviceRevisionConflict('device_rollback_review_changed')
            self._admit(admission)
            self.store._archive(content)
            record={'schema':SCHEMA,'profile':self.profile,'operation':fresh.operation,
                    'before':fresh.current_revision,'after':fresh.resulting_revision,
                    'identity':[list(item) for item in fresh._identity],'state':'prepared',
                    'previous':{key:previous[key] for key in fence.FIELDS}}
            try:
                atomic_private(self.journal,_encode(record))
                atomic_private(self.store.path,fresh._encoded)
                persisted,checked=self._read()
                if _sha(persisted)!=fresh.resulting_revision or self._identity(checked)!=fresh._identity:
                    raise DeviceWorkflowError('device_rollback_readback_mismatch')
                self._verify_operation_resources(checked,fresh.operation)
                record.update(state='rolled_back',previous=None)
                atomic_private(self.journal,_encode(record))
            except (OSError,ValueError,RuntimeError):
                raise DeviceOutcomeUncertain('device_rollback_outcome_uncertain') from None
            if fresh.resulting_revision!=previous['before']:
                return dict(fresh.public(),outcome='prior_policy_restored_model_space_guard_retained',model_space_guard_retained=True)
            return dict(fresh.public(),outcome='prior_bytes_restored_and_read_back')

    def reconcile(self,expected,*,admission=None):
        """Explicit persisted-byte reconciliation; no retry, rollback or restart."""
        with self.store._locked(allow_device_pending=True):
            content,current=self._read()
            if not isinstance(expected,str) or not _SHA.fullmatch(expected) or _sha(content)!=expected:
                raise DeviceRevisionConflict('device_configuration_changed')
            record=fence.read(self.store.path,durable=True)
            if (record is None or record['profile']!=self.profile or record['state']!='prepared'
                    or tuple(tuple(row) for row in record['identity'])!=self._identity(current)):
                raise DeviceWorkflowError('device_intent_not_reconcilable')
            if expected==record['after']:
                _,before=self._archived(record['before'])
                operation=record['operation']
                if operation.endswith('_rollback'):
                    self._assert_operation_scope(operation.removesuffix('_rollback'),current,before)
                else:
                    self._assert_operation_scope(operation,before,current)
                self._verify_operation_resources(current,operation)
                record.update(state='rolled_back' if operation.endswith('_rollback') else 'saved',previous=None)
                outcome='current_saved_revision_reconciled'
            elif expected==record['before']:
                previous=record['previous']
                record=(dict(previous,previous=None) if previous is not None else
                        dict(record,state='abandoned',previous=None))
                outcome='pre_save_revision_observed_intent_retired'
            else:
                raise DeviceWorkflowError('device_intent_requires_owner_recovery')
            self._admit(admission)
            try:
                atomic_private(self.journal,_encode(record))
                fence.check(self.store.path)
            except (OSError,ValueError):
                raise DeviceOutcomeUncertain('device_reconciliation_outcome_uncertain') from None
            return {'schema':SCHEMA,'profile':self.profile,'revision':expected,
                    'outcome':outcome,'activation':'saved_not_active_runtime',
                    'configuration_rewritten':False,'runtime_restarted':False}

    def transaction_status(self):
        content,current=self._read()
        record=fence.read(self.store.path)
        from .host_activation import read as host_activation
        reserved=host_activation(self.store.path)
        result={'schema':SCHEMA,'profile':self.profile,'revision':_sha(content),
                'pending':record is not None and record['state']=='prepared',
                'rollback_available':False,'reconcile_available':False,
                'activation':'saved_not_active_runtime'}
        result['host_activation_pending']=reserved is not None and reserved['phase']=='prepared'
        if result['host_activation_pending']:
            return result
        if record is None:
            return result
        if record['profile']!=self.profile:
            raise DeviceWorkflowError('device_transaction_profile_mismatch')
        if result['pending']:
            result['reconcile_available']=(_sha(content) in (record['before'],record['after'])
                and self._identity(current)==tuple(tuple(row) for row in record['identity']))
        elif record['state']=='saved':
            try:
                self._prepare_rollback(_sha(content),content,current,record)
                result['rollback_available']=True
            except (OSError,ValueError,RuntimeError):
                pass
        return result

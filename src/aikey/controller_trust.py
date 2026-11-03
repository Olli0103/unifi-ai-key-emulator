"""Immutable profile-bound controller trust objects for reviewed device workflows.

Preparing trust performs no IO or connection. Installing an object does not
activate it: a separately reviewed atomic configuration switch selects it.
Rollback can select the retained prior object without rewriting trust/key files.
Device certificates/private keys and legacy trust files are never modified here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .protocol import _json_object

SCHEMA='aikey-controller-trust/1'
DIRECTORY='controller-trust'
_REFERENCE=re.compile(r'controller-trust\.([0-9a-f]{64})\.json\Z')
_MAC=re.compile(r'[0-9A-F]{12}\Z')
_PIN=re.compile(r'[0-9a-f]{64}\Z')
_HOST=re.compile(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}\Z')
_PEM=re.compile(r'-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+\n-----END CERTIFICATE-----\n?\Z')
_LIMIT=32768


class ControllerTrustError(ValueError):
    """Fixed failure codes; no host, certificate, path or secret in errors."""


@dataclass(frozen=True)
class TrustObject:
    reference: str
    raw: bytes
    host: str
    fingerprint: str
    device_mac: str
    purpose: str
    certificate_pem: str


def valid_reference(value):
    return isinstance(value,str) and _REFERENCE.fullmatch(value) is not None


def _certificate(pem, fingerprint):
    if (not isinstance(pem,str) or len(pem)>16384 or not _PEM.fullmatch(pem)
            or not isinstance(fingerprint,str) or not _PIN.fullmatch(fingerprint)):
        raise ControllerTrustError('invalid_controller_certificate')
    try:
        certificate=x509.load_pem_x509_certificate(pem.encode('ascii'))
        actual=certificate.fingerprint(hashes.SHA256()).hex()
        now=datetime.now(timezone.utc)
        if (actual!=fingerprint or not certificate.not_valid_before_utc<=now<=certificate.not_valid_after_utc):
            raise ValueError
    except (ValueError,TypeError,UnicodeError):
        raise ControllerTrustError('invalid_controller_certificate') from None


def prepare(*,host,device_mac,purpose,certificate_pem,fingerprint):
    """A pure candidate; the owner must display its destination/pin for review."""
    if (not isinstance(host,str) or not _HOST.fullmatch(host)
            or not isinstance(device_mac,str) or not _MAC.fullmatch(device_mac)
            or purpose not in ('control','search')):
        raise ControllerTrustError('invalid_controller_trust_binding')
    if isinstance(certificate_pem,str):
        # Native HTML multiline form submission uses CRLF. Canonicalize only
        # that encoding difference, not arbitrary surrounding payloads.
        certificate_pem=certificate_pem.replace('\r\n','\n')
    if isinstance(fingerprint,str):
        fingerprint=fingerprint.replace(':','').lower()
    _certificate(certificate_pem,fingerprint)
    value={'schema':SCHEMA,'host':host,'device_mac':device_mac,'purpose':purpose,
           'certificate_pem':certificate_pem,'fingerprint':fingerprint}
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    reference='controller-trust.'+hashlib.sha256(raw).hexdigest()+'.json'
    return TrustObject(reference,raw,host,fingerprint,device_mac,purpose,certificate_pem)


def _root(root):
    descriptor=os.open(Path(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(descriptor)
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:
        os.close(descriptor)
        raise ControllerTrustError('controller_trust_directory_not_private')
    return descriptor


def _read(directory,reference,*,durable=False):
    if not isinstance(reference,str) or not _REFERENCE.fullmatch(reference):
        raise ControllerTrustError('invalid_controller_trust_reference')
    descriptor=os.open(reference,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
    with os.fdopen(descriptor,'rb') as source:
        info=os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                or info.st_mode&0o077 or not 1<=info.st_size<=_LIMIT):
            raise ControllerTrustError('controller_trust_file_not_private')
        raw=source.read(_LIMIT+1)
        if len(raw)>_LIMIT or hashlib.sha256(raw).hexdigest()!=_REFERENCE.fullmatch(reference)[1]:
            raise ControllerTrustError('controller_trust_digest_mismatch')
        if durable:
            os.fsync(source.fileno())
    return raw


def install(root, candidate):
    """Exclusive immutable publication only; never update config or old trust."""
    if not isinstance(candidate,TrustObject):
        raise ControllerTrustError('invalid_controller_trust_candidate')
    # Dataclass construction alone is not authority to write arbitrary bytes.
    checked=prepare(host=candidate.host,device_mac=candidate.device_mac,
        purpose=candidate.purpose,certificate_pem=candidate.certificate_pem,
        fingerprint=candidate.fingerprint)
    if checked!=candidate:
        raise ControllerTrustError('invalid_controller_trust_candidate')
    directory=-1
    try:
        directory=_root(root)
        try:
            descriptor=os.open(candidate.reference,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                               0o600,dir_fd=directory)
        except FileExistsError:
            if _read(directory,candidate.reference)!=candidate.raw:
                raise ControllerTrustError('controller_trust_conflict')
        else:
            with os.fdopen(descriptor,'wb') as output:
                os.fchmod(output.fileno(),0o600)
                output.write(candidate.raw)
                output.flush()
                os.fsync(output.fileno())
        if _read(directory,candidate.reference,durable=True)!=candidate.raw:
            raise ControllerTrustError('controller_trust_readback_mismatch')
        # Even an existing staged object may come from an earlier failed fsync.
        # Exact contents alone do not establish durable producer handoff.
        os.fsync(directory)
    except OSError:
        # A failed publication may leave an unusable object; never select it or
        # overwrite an existing object, and never claim a config was applied.
        raise ControllerTrustError('controller_trust_publication_failed') from None
    finally:
        if directory>=0:
            os.close(directory)
    return candidate.reference


def load(root,reference,*,host,device_mac,purpose,fingerprint):
    directory=-1
    try:
        directory=_root(root)
        raw=_read(directory,reference)
        value=_json_object(raw)
        if (set(value)!={'schema','host','device_mac','purpose','certificate_pem','fingerprint'}
                or value['schema']!=SCHEMA or value['host']!=host
                or value['device_mac']!=device_mac or value['purpose']!=purpose
                or value['fingerprint']!=fingerprint):
            raise ControllerTrustError('controller_trust_binding_mismatch')
        candidate=prepare(host=host,device_mac=device_mac,purpose=purpose,
                          certificate_pem=value['certificate_pem'],fingerprint=fingerprint)
        if candidate.reference!=reference or candidate.raw!=raw:
            raise ControllerTrustError('controller_trust_encoding_mismatch')
        return candidate
    except (OSError,ValueError,TypeError):
        raise ControllerTrustError('controller_trust_unavailable') from None
    finally:
        if directory>=0:
            os.close(directory)


def context(candidate,*,check_hostname=True):
    """Normal certificate validation stays enabled, with TLS 1.2 minimum."""
    if not isinstance(candidate,TrustObject) or type(check_hostname) is not bool:
        raise ControllerTrustError('invalid_controller_trust_candidate')
    checked=prepare(host=candidate.host,device_mac=candidate.device_mac,purpose=candidate.purpose,
                    certificate_pem=candidate.certificate_pem,fingerprint=candidate.fingerprint)
    if candidate!=checked:
        raise ControllerTrustError('invalid_controller_trust_candidate')
    result=ssl.create_default_context(cadata=candidate.certificate_pem)
    result.minimum_version=ssl.TLSVersion.TLSv1_2
    result.check_hostname=check_hostname
    return result


def selected(config,*,kind,state_dir,channel='control'):
    """Resolve only explicitly selected trust; absent legacy selection is None.

    A broken selected object is a hard refusal, never fallback to another root.
    Metadata must match the current profile, host and existing connector pin.
    """
    if kind not in ('aikey','aiport') or channel not in ('control','search'):
        raise ControllerTrustError('invalid_controller_trust_binding')
    try:
        if kind=='aiport':
            if channel!='control':
                raise ControllerTrustError('invalid_controller_trust_binding')
            reference=config.get('controller_trust_ref')
            host,pin,mac=config.get('controller_ip'),config.get('controller_pin'),config.get('mac')
        else:
            controller=config['controller']
            reference=controller.get('search_trust_ref' if channel=='search' else 'trust_ref')
            if reference is None:
                return None  # legacy contexts need not carry device metadata
            host=controller.get('host')
            pin=controller.get('search_expected_fingerprint' if channel=='search' else 'expected_fingerprint')
            mac=config['device']['mac']
        if reference is None:
            return None
        if not valid_reference(reference) or not isinstance(pin,str):
            raise ControllerTrustError('invalid_controller_trust_binding')
        from .aiport_ingest import normalize_mac
        return load(Path(state_dir)/DIRECTORY,reference,host=host,device_mac=normalize_mac(mac),purpose=channel,
                    fingerprint=pin.replace(':','').lower())
    except (KeyError,TypeError,ValueError):
        raise ControllerTrustError('controller_trust_unavailable') from None

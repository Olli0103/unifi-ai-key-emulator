"""Synthetic PKI, immutable trust publication and real local TLS refusal."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from aikey.controller_trust import ControllerTrustError,context,install,load,prepare


def certificate(*,expired=False):
    key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fictional-controller.invalid')])
    now=datetime.now(timezone.utc)
    import ipaddress
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=2))
        .not_valid_after(now+timedelta(days=-1 if expired else 1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),cert.fingerprint(hashes.SHA256()).hex(),
            key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))


@pytest.fixture
def candidate():
    pem,pin,_=certificate()
    return prepare(host='127.0.0.1',device_mac='020000000001',purpose='control',certificate_pem=pem,fingerprint=pin)


def bound(root,candidate,**overrides):
    values={name:getattr(candidate,name) for name in ('host','device_mac','purpose','fingerprint')}
    return load(root,candidate.reference,**dict(values,**overrides))


def test_preparation_is_pure_and_publication_cannot_activate_or_rewrite_identity(tmp_path,candidate):
    existing={'config.json':b'fictional unchanged configuration','device.key':b'fixture identity bytes',
              'device.crt':b'fixture certificate bytes','controller-ca.pem':b'previous trust bytes'}
    for name,raw in existing.items():
        (tmp_path/name).write_bytes(raw)
    assert set(p.name for p in tmp_path.iterdir())==set(existing)
    reference=install(tmp_path,candidate)
    assert bound(tmp_path,candidate)==candidate
    first=(tmp_path/reference).stat()
    assert install(tmp_path,candidate)==reference
    second=(tmp_path/reference).stat()
    assert (first.st_ino,first.st_mtime_ns)==(second.st_ino,second.st_mtime_ns)
    assert second.st_mode&0o777==0o600
    assert all((tmp_path/name).read_bytes()==raw for name,raw in existing.items())


@pytest.mark.parametrize('change',[{'host':'other.invalid'},{'device_mac':'020000000002'},
                                  {'purpose':'search'},{'fingerprint':'a'*64}])
def test_installed_object_is_bound_to_its_exact_profile_destination_and_role(tmp_path,candidate,change):
    install(tmp_path,candidate)
    with pytest.raises(ControllerTrustError,match='unavailable'):
        bound(tmp_path,candidate,**change)


@pytest.mark.parametrize('fault',['parent-link','leaf-link','fifo','mode','tamper','forged-object'])
def test_unsafe_objects_refuse_without_overwriting_any_existing_leaf(tmp_path,candidate,fault):
    root=tmp_path/'root'
    root.mkdir(mode=0o700)
    selected=root
    if fault=='parent-link':
        selected=tmp_path/'alias'
        selected.symlink_to(root,target_is_directory=True)
    elif fault=='forged-object':
        candidate=replace(candidate,reference='../escape.json')
    else:
        path=root/candidate.reference
        if fault=='leaf-link':
            target=tmp_path/'retained'
            target.write_bytes(b'unchanged')
            path.symlink_to(target)
        elif fault=='fifo':
            os.mkfifo(path,0o600)
        else:
            path.write_bytes(candidate.raw if fault=='mode' else b'tampered')
            path.chmod(0o644 if fault=='mode' else 0o600)
    with pytest.raises(ControllerTrustError):
        install(selected,candidate)
    assert not (tmp_path/'escape.json').exists()
    if fault=='leaf-link':
        assert target.read_bytes()==b'unchanged'


def test_duplicate_keys_are_not_authorized_by_a_matching_filename_digest(tmp_path,candidate):
    raw=candidate.raw.replace(b'"host":',b'"host":"unexpected.invalid","host":')
    name='controller-trust.'+hashlib.sha256(raw).hexdigest()+'.json'
    p=tmp_path/name
    p.write_bytes(raw)
    p.chmod(0o600)
    with pytest.raises(ControllerTrustError):
        load(tmp_path,name,host=candidate.host,device_mac=candidate.device_mac,
             purpose=candidate.purpose,fingerprint=candidate.fingerprint)


def test_certificate_expiry_wrong_pin_and_private_key_payload_are_refused():
    pem,pin,key=certificate()
    expired,expired_pin,_=certificate(expired=True)
    for body,expected in ((pem,'0'*64),(expired,expired_pin),(pem+key.decode(),pin),(pem+pem,pin)):
        with pytest.raises(ControllerTrustError) as failure:
            prepare(host='127.0.0.1',device_mac='020000000001',purpose='control',
                    certificate_pem=body,fingerprint=expected)
        assert '127.0.0.1' not in str(failure.value) and pin not in str(failure.value)


def test_fsync_failure_is_not_reported_as_applied_and_does_not_select_a_pointer(tmp_path,candidate,monkeypatch):
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'controller':'old'}))
    before=config.read_bytes()
    def broken(_):
        raise OSError('synthetic fsync refusal')
    monkeypatch.setattr(os,'fsync',broken)
    with pytest.raises(ControllerTrustError,match='publication_failed'):
        install(tmp_path,candidate)
    assert config.read_bytes()==before


def test_existing_staged_trust_must_also_be_durable_before_selection(tmp_path,candidate,monkeypatch):
    install(tmp_path,candidate)
    def broken(_):
        raise OSError('synthetic fsync refusal')
    monkeypatch.setattr(os,'fsync',broken)
    with pytest.raises(ControllerTrustError,match='publication_failed'):
        install(tmp_path,candidate)


def test_native_multiline_form_certificate_has_the_same_canonical_binding(candidate):
    value=prepare(host=candidate.host,device_mac=candidate.device_mac,purpose=candidate.purpose,
        certificate_pem=candidate.certificate_pem.replace('\n','\r\n'),fingerprint=candidate.fingerprint)
    assert value==candidate


def test_real_tls_connection_accepts_only_the_selected_controller_certificate(tmp_path):
    pem,pin,key=certificate()
    other,other_pin,_=certificate()
    (tmp_path/'cert.pem').write_text(pem)
    (tmp_path/'key.pem').write_bytes(key)
    server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path/'cert.pem',tmp_path/'key.pem')
    def selected(body,fingerprint):
        value=prepare(host='127.0.0.1',device_mac='020000000001',purpose='control',
                      certificate_pem=body,fingerprint=fingerprint)
        install(tmp_path,value)
        return context(bound(tmp_path,value))
    good=selected(pem,pin)
    wrong=selected(other,other_pin)
    assert good.verify_mode==ssl.CERT_REQUIRED and good.check_hostname
    assert good.minimum_version>=ssl.TLSVersion.TLSv1_2
    async def scenario():
        async def serve(reader,writer):
            writer.write(b'fixture')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server=await asyncio.start_server(serve,'127.0.0.1',0,ssl=server_context)
        try:
            port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',port,ssl=good,server_hostname='127.0.0.1')
            assert await reader.read()==b'fixture'
            writer.close()
            await writer.wait_closed()
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.open_connection('127.0.0.1',port,ssl=wrong,server_hostname='127.0.0.1')
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())

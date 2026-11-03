"""Real loopback TLS tests: anonymous peer pin only, zero application bytes."""
import asyncio
from dataclasses import replace
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from aikey.controller_trust import prepare
from aikey.controller_trust_probe import probe
from test_controller_trust import certificate


async def tls_server(tmp_path, *, different_leaf=False):
    pem, pin, key = certificate()
    serving_pem = pem
    if different_leaf:
        root = x509.load_pem_x509_certificate(pem.encode())
        root_key = serialization.load_pem_private_key(key, password=None)
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fictional-leaf.invalid')])
        leaf = (x509.CertificateBuilder().subject_name(subject).issuer_name(root.subject)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(root.not_valid_before_utc).not_valid_after(root.not_valid_after_utc)
            .add_extension(root.extensions.get_extension_for_class(x509.SubjectAlternativeName).value, critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(root_key, hashes.SHA256()))
        serving_pem = leaf.public_bytes(serialization.Encoding.PEM).decode() + pem
        key = leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    (tmp_path / 'cert.pem').write_text(serving_pem)
    (tmp_path / 'key.pem').write_bytes(key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / 'cert.pem', tmp_path / 'key.pem')
    context.verify_mode = ssl.CERT_OPTIONAL
    seen = []
    completed = asyncio.Event()
    async def accept(reader, writer):
        try:
            seen.append({'client_certificate': writer.get_extra_info('peercert'),
                         'application_bytes': await reader.read(1)})
        finally:
            writer.close()
            await writer.wait_closed()
            completed.set()
    server = await asyncio.start_server(accept, '127.0.0.1', 0, ssl=context)
    trust = prepare(host='127.0.0.1', device_mac='020000000001', purpose='control',
                    certificate_pem=pem, fingerprint=pin)
    return server, trust, server.sockets[0].getsockname()[1], seen, completed


@pytest.mark.parametrize('match', [True, False])
async def test_real_handshake_sends_no_client_certificate_or_http_and_enforces_peer_pin(tmp_path, match):
    server, trust, port, seen, completed = await tls_server(tmp_path, different_leaf=not match)
    async with server:
        report = await probe([(trust, port)], verify_hostname=True)
        await asyncio.wait_for(completed.wait(), 3)
    assert report['matched'] is match
    assert report['channels'][0]['outcome'] == ('matched' if match else 'fingerprint_mismatch')
    assert seen == [{'client_certificate': None, 'application_bytes': b''}]
    assert report['device_credentials_sent'] is False and report['http_requests_sent'] == 0
    assert report['native_adoption_verified'] is False and report['configuration_saved'] is False
    assert report['automatic_retry'] is False


async def test_untrusted_server_is_not_retried_with_legacy_or_insecure_context(tmp_path):
    server, _, port, seen, _ = await tls_server(tmp_path)
    pem, pin, _ = certificate()
    other = prepare(host='127.0.0.1', device_mac='020000000001', purpose='control', certificate_pem=pem, fingerprint=pin)
    async with server:
        report = await probe([(other, port)], verify_hostname=True)
    assert report['matched'] is False and report['channels'][0]['outcome'] == 'certificate_verification_failed'
    assert report['channels'][0]['client_attempts'] == 1 and seen == []


async def test_expired_authority_does_not_contact_second_channel(tmp_path):
    server, trust, port, seen, completed = await tls_server(tmp_path)
    calls = []
    def admission():
        calls.append(True)
        return len(calls) == 1
    async with server:
        with pytest.raises(ValueError, match='authority_expired'):
            await probe([(trust, port), (replace(trust, purpose='search'), port)],
                        verify_hostname=True, admission=admission)
        await asyncio.wait_for(completed.wait(), 3)
    assert len(calls) == 2 and len(seen) == 1


async def test_invalid_unbounded_or_duplicate_targets_refuse_before_network():
    pem, pin, _ = certificate()
    trust = prepare(host='127.0.0.1', device_mac='020000000001', purpose='control', certificate_pem=pem, fingerprint=pin)
    for targets in ([], [(trust, True)], [(trust, 0)], [(trust, 65536)], [(trust, 7442)] * 2, [(trust, 7442)] * 3):
        with pytest.raises(ValueError, match='invalid_controller_probe'):
            await probe(targets, verify_hostname=True)

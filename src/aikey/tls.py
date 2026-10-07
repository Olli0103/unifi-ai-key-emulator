"""Persistent emulator certificates and explicit controller trust."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
from pathlib import Path
import re
import socket
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .config import ConfigError, atomic_private


def ensure_identity_certificate(state_dir: Path, mac: str) -> tuple[Path, Path]:
    cert_path, key_path = state_dir / "device.crt", state_dir / "device.key"
    if cert_path.exists() != key_path.exists():
        raise ConfigError("Incomplete TLS identity; recover the missing file instead of replacing it")
    if cert_path.exists():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"local-aikey-{mac}")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False, key_agreement=False,
                key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None),
                critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False)
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False)
            .sign(key, hashes.SHA256()))
    atomic_private(key_path, key.private_bytes(serialization.Encoding.PEM,
                   serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    atomic_private(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(state_dir: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(state_dir / "device.crt", state_dir / "device.key")
    return context


def client_context(config: dict, *, ca_file: str | None = None) -> ssl.SSLContext:
    controller = config["controller"]
    context = ssl.create_default_context(cafile=ca_file or controller["ca_file"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # A directly trusted console certificate can have a hostname that differs from its LAN IP.
    context.check_hostname = controller.get("verify_hostname", True)
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    state = Path(config["runtime"]["state_dir"])
    context.load_cert_chain(state / "device.crt", state / "device.key")
    return context


def import_controller_trust(host: str, port: int, expected_sha256: str, output: Path) -> str:
    """Fetch only a TLS certificate. No HTTP request, credentials or adoption are sent."""
    normalized = expected_sha256.replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ConfigError("Supply the independently verified controller SHA-256 fingerprint")
    if output.exists():
        raise ConfigError("Controller trust file already exists; refusing to overwrite it")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    # The explicit hash below authenticates this one certificate before it is persisted.
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    actual = hashlib.sha256(der).hexdigest()
    if not hmac.compare_digest(actual, normalized):
        raise ConfigError("Controller certificate fingerprint does not match; no trust was saved")
    atomic_private(output, ssl.DER_cert_to_PEM_cert(der))
    return actual

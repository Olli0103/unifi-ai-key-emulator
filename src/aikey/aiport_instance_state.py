"""Provision one stable, private AI Port identity from an addressed plan slot.

This does not start a container, claim adoption, or pair any camera. Existing
state is verified and never rewritten; incomplete state requires recovery.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import ssl
import stat

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .aiport_candidate import CandidateError, _private_file, _private_ipv4, load_config
from .config import atomic_private
from .tls import ensure_identity_certificate


_PIN = re.compile(r"[0-9a-fA-F]{64}\Z")
_BASE_KEYS = frozenset({"controller_ip", "device_ip", "mac", "controller_pin",
                        "firmware_version"})


class InstanceStateError(ValueError):
    """A planned instance has no safe, stable private identity."""


def _slot_address(plan: dict, slot: int) -> str:
    if (not isinstance(plan, dict) or plan.get("schema") != "aikey-aiport-deployment-plan/2"
            or not isinstance(plan.get("instances"), list)
            or type(slot) is not int or not 1 <= slot <= len(plan["instances"])):
        raise InstanceStateError("A valid addressed AI Port plan slot is required")
    item = plan["instances"][slot - 1]
    if (not isinstance(item, dict) or type(item.get("slot")) is not int
            or item["slot"] != slot
            or item.get("source_kind") not in {"protect", "onvif"}
            or not isinstance(item.get("camera_ids"), list)
            or not item["camera_ids"]):
        raise InstanceStateError("Invalid AI Port plan slot")
    try:
        if not isinstance(item.get("host_ip"), str):
            raise CandidateError("Candidate requires an explicit IPv4 address")
        address = _private_ipv4(item.get("host_ip"))
        key_ip = plan.get("ai_key", {}).get("host_ip")
        if key_ip is not None and address == _private_ipv4(key_ip):
            raise InstanceStateError("AI Port and AI Key require distinct addresses")
    except (CandidateError, AttributeError) as exc:
        raise InstanceStateError("AI Port slot needs a private LAN address") from exc
    return address


def _trusted_controller(cert_file: Path, pin: str) -> bytes:
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        raise InstanceStateError("A pinned controller certificate is required")
    try:
        pem = _private_file(cert_file, 16384)
        der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
        certificate = x509.load_der_x509_certificate(der)
    except (CandidateError, UnicodeError, ssl.SSLError, ValueError) as exc:
        raise InstanceStateError("Invalid private controller certificate") from exc
    now = datetime.now(timezone.utc)
    if (not hmac.compare_digest(hashlib.sha256(der).digest(), bytes.fromhex(pin))
            or not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc):
        raise InstanceStateError("Controller certificate does not match pin or validity")
    return pem


def _verify_existing(state_dir: Path, expected: dict, pem: bytes) -> None:
    info = state_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise InstanceStateError("Existing AI Port state directory must be private")
    try:
        # The saved decoder path belongs to the Linux container, not the host
        # running plan and identity verification. Runtime startup checks it.
        config = load_config(state_dir / "config.json", check_decoder_executable=False)
        identity = json.loads(_private_file(state_dir / "identity.json", 4096))
        saved_ca = _private_file(state_dir / "controller-ca.pem", 16384)
        saved_cert = _private_file(state_dir / "device.crt", 16384)
        _private_file(state_dir / "device.key", 16384)
        cert = x509.load_pem_x509_certificate(saved_cert)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(state_dir / "device.crt", state_dir / "device.key")
    except (CandidateError, OSError, ValueError, ssl.SSLError, UnicodeError,
            IndexError) as exc:
        raise InstanceStateError("Existing AI Port identity is incomplete or invalid") from exc
    if (not _BASE_KEYS <= set(config) or not isinstance(identity, dict)
            or identity.get("mac") != config["mac"]
            or any(config.get(name) != value for name, value in expected.items())
            or not hmac.compare_digest(saved_ca, pem)
            or not cert.not_valid_before_utc <= datetime.now(timezone.utc)
            <= cert.not_valid_after_utc
            or (identity.get("certificate_sha256") is not None
                and identity["certificate_sha256"] != hashlib.sha256(
                    cert.public_bytes(serialization.Encoding.DER)).hexdigest())):
        raise InstanceStateError("Existing AI Port identity differs from the planned slot")


def provision_slot(plan: dict, slot: int, state_dir: Path, *, controller_ip: str,
                   controller_cert_file: Path, controller_pin: str,
                   firmware_version: str = "5.1.12", mac: str | None = None) -> dict:
    """Create once or verify, never rotate an already created identity.

    ``mac`` fixes the new identity's MAC (a planned slot's reviewed value);
    an existing identity must already have it.
    """
    if mac is not None and not re.fullmatch(r"02[0-9A-F]{10}", mac):
        raise InstanceStateError("A planned MAC must be locally administered")
    address = _slot_address(plan, slot)
    try:
        controller_ip = _private_ipv4(controller_ip)
    except CandidateError as exc:
        raise InstanceStateError("Controller needs a private LAN address") from exc
    if controller_ip == address:
        raise InstanceStateError("Controller and AI Port require distinct addresses")
    pem = _trusted_controller(controller_cert_file, controller_pin)
    if (not isinstance(firmware_version, str)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", firmware_version)):
        raise InstanceStateError("Invalid AI Port compatibility version")
    expected = {"controller_ip": controller_ip, "device_ip": address,
                "controller_pin": controller_pin.lower(),
                "firmware_version": firmware_version}
    state_dir = Path(state_dir)
    if state_dir.is_symlink():
        raise InstanceStateError("AI Port state directory cannot be a symlink")
    if state_dir.exists():
        _verify_existing(state_dir, expected, pem)
        if mac is not None and json.loads((state_dir / "config.json").read_text()).get("mac") != mac:
            raise InstanceStateError("The existing identity has a different MAC")
        return {"slot": slot, "state": "verified_existing", "host_ip": address}
    if (not state_dir.parent.is_dir() or state_dir.parent.is_symlink()
            or state_dir.parent.stat().st_mode & 0o077):
        raise InstanceStateError("Create a private parent directory before provisioning")
    try:
        state_dir.mkdir(mode=0o700)
        mac = mac or (bytes([0x02]) + secrets.token_bytes(5)).hex().upper()
        ensure_identity_certificate(state_dir, mac)
        certificate = x509.load_pem_x509_certificate(
            _private_file(state_dir / "device.crt", 16384))
        fingerprint = hashlib.sha256(certificate.public_bytes(
            serialization.Encoding.DER)).hexdigest()
        atomic_private(state_dir / "identity.json", json.dumps({
            "mac": mac, "certificate_sha256": fingerprint}) + "\n")
        atomic_private(state_dir / "controller-ca.pem", pem)
        atomic_private(state_dir / "config.json", json.dumps({**expected, "mac": mac}) + "\n")
        _verify_existing(state_dir, expected, pem)
    except (OSError, CandidateError, ValueError, ssl.SSLError) as exc:
        # Leave incomplete state in place for explicit recovery. Never replace
        # a certificate or MAC on retry after an interrupted provision.
        raise InstanceStateError("AI Port state provisioning failed; inspect incomplete state") from exc
    return {"slot": slot, "state": "created", "host_ip": address}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision one addressed AI Port identity without adoption")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--controller-cert-file", type=Path, required=True)
    parser.add_argument("--controller-pin", required=True)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(_private_file(args.plan, 2 * 1024 * 1024))
        result = provision_slot(plan, args.slot, args.state_dir,
                                controller_ip=args.controller,
                                controller_cert_file=args.controller_cert_file,
                                controller_pin=args.controller_pin)
    except (InstanceStateError, CandidateError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

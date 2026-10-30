"""Private saved-identity/adoption/trust observations, never runtime inference.

No initialization, credential hydration, migration, controller request or key
read. A persisted adopted flag is not a current verified controller connection.
"""

from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .aiport_ingest import normalize_mac
from .config_store import ConfigurationStore, ConfigurationStoreError
from .device import _parse_pin
from .protocol import _json_object
from .state_backup_preflight import _host
from .state_schema import check_schema


def _read(path, limit=1024 * 1024):
    return _json_object(ConfigurationStore._read_regular_bytes(path, limit))


def _certificate(path):
    raw = ConfigurationStore._read_regular_bytes(path, 262144)
    cert = x509.load_pem_x509_certificate(raw)
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    now = datetime.now(timezone.utc)
    return {"sha256": digest, "valid_now": cert.not_valid_before_utc <= now <= cert.not_valid_after_utc,
            "expires_at": cert.not_valid_after_utc.isoformat()}


def _trust(kind, directory, config):
    controller = (config.get("controller") or {}) if kind == "aikey" else config
    path = (_host(controller.get("ca_file"), directory,
                  (config.get("runtime") or {}).get("state_dir"))
            if kind == "aikey" else directory / "controller-ca.pem")
    result = {"certificate_valid_now": None, "pin_matches_saved_certificate": None,
              "live_connection_checked": False}
    if path is None:
        return {**result, "observation": "not_configured"}
    try:
        cert = _certificate(path)
        pin = _parse_pin(controller.get("expected_fingerprint" if kind == "aikey" else "controller_pin"))
        result.update(certificate_valid_now=cert["valid_now"], expires_at=cert["expires_at"],
                      observation="saved_certificate")
        if pin is not None:
            result["pin_matches_saved_certificate"] = hmac.compare_digest(bytes.fromhex(cert["sha256"]), pin)
    except (ConfigurationStoreError, OSError, ValueError, TypeError):
        result["observation"] = "unavailable"
    return result


def identity_status(kind, directory, config):
    directory = Path(directory)
    mac = normalize_mac(config["device"]["mac"] if kind == "aikey" else config["mac"])
    result = {"configured_mac": mac, "persisted_state": "not_present", "persisted_adoption": None,
              "live_connection_checked": False,
              "adoption_identity_binding": ("checked_against_state_mac" if kind == "aikey"
                                            else "controller_binding_only_no_mac_in_adoption_record"),
              "saved_pairing_count": None if kind == "aikey" else len(config.get("paired_streams", [])),
              "controller_trust": _trust(kind, directory, config)}
    try:
        cert = _certificate(directory / "device.crt")
        result["identity_certificate"] = {"observation": "saved_certificate", **cert}
    except (ConfigurationStoreError, OSError, ValueError):
        result["identity_certificate"] = {"observation": "unavailable"}
    path = directory / ("device-state.json" if kind == "aikey" else "aiport-adoption.json")
    # lexists-equivalent lstat does not follow an adoption-state symlink.
    try:
        path.lstat()
    except FileNotFoundError:
        return result
    except OSError:
        result["persisted_state"] = "unavailable"
        return result
    try:
        value = _read(path)
        if kind == "aikey":
            check_schema(value)
            if normalize_mac(value.get("mac")) != mac:
                result["persisted_state"] = "binding_mismatch"
                return result
            if type(value.get("adopted")) is not bool:
                raise ValueError
            adopted = value["adopted"]
        else:
            phase = value.get("phase")
            keys = {"phase", "controller_ip", "controller_pin", "control_port"}
            if phase == "pending":
                keys |= {"token", "expires_at"}
                if (not isinstance(value.get("token"), str) or not 16 <= len(value["token"]) <= 512
                        or type(value.get("expires_at")) is not int):
                    raise ValueError
            elif phase != "adopted":
                raise ValueError
            if (set(value) != keys or type(value.get("control_port")) is not int
                    or not 1 <= value["control_port"] <= 65535):
                raise ValueError
            if (value["controller_ip"] != config["controller_ip"]
                    or _parse_pin(value["controller_pin"]) != _parse_pin(config["controller_pin"])):
                result["persisted_state"] = "binding_mismatch"
                return result
            adopted = phase == "adopted"
        result.update(persisted_state="saved_adoption_record", persisted_adoption=adopted)
    except (ConfigurationStoreError, OSError, ValueError, TypeError, KeyError):
        result["persisted_state"] = "unavailable"
    return result

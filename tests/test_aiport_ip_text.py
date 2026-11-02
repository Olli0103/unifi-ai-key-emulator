"""Address policies require text, never ipaddress's integer/packed coercions."""

import ipaddress
import json
import sys
import time

import pytest

from aikey.aiport_candidate import CandidateError, _private_ipv4, parse_config
from aikey.aiport_ingest import AiPortIngress, IngressError, private_source_ip
from aikey.aiport_instance_state import InstanceStateError, provision_slot
from test_aiport_candidate import fixture_state
from test_aiport_instance_state import fixture


@pytest.mark.parametrize("address", [
    True, 3232238081, b"\xc0\xa8\x0a\x01", ipaddress.IPv4Address("192.168.10.1"),
])
@pytest.mark.parametrize("validator,error", [
    (_private_ipv4, CandidateError), (private_source_ip, IngressError),
])
def test_nontext_values_are_not_implicitly_network_addresses(address, validator, error):
    with pytest.raises(error):
        validator(address)


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.2.3", "192.168.10.1"])
def test_explicit_private_text_is_unchanged(address):
    assert _private_ipv4(address) == address
    assert private_source_ip(address) == address


@pytest.mark.parametrize("address", [True, 3232238081])
@pytest.mark.parametrize("field", [
    "controller_ip", "device_ip", "paired_stream", "paired_streams",
    "diagnostic_stream", "diagnostic_streams",
])
def test_actual_candidate_json_rejects_nontext_address_fields(tmp_path, address, field):
    config = fixture_state(tmp_path)
    if field not in {"controller_ip", "device_ip"}:
        stream = {"camera_mac": "2A1122334455", "source_ip": "192.168.10.1",
                  "ffmpeg_path": sys.executable}
        if field.endswith("s"):
            config[field] = [stream, {**stream, "camera_mac": "2A1122334456",
                                     "source_ip": "192.168.10.1"}]
        else:
            config[field] = stream
        if field.startswith("diagnostic"):
            config["diagnostic_hello_until"] = int(time.time()) + 120
        if field == "paired_streams":
            config["live_pool_detector"] = {
                "inference_backend": "vision_api", "threshold": 0.8,
                "smart_types": ["person"],
                "provider_config": {"provider": "openai", "model": "fictional-only",
                                    "allow_remote": True, "max_output_tokens": 256},
            }
    # Prove each fixture reaches a valid complete policy before changing only
    # the selected address. Missing prerequisites must not fake this refusal.
    assert parse_config(json.dumps(config).encode(), state_dir=tmp_path)
    if field in {"controller_ip", "device_ip"}:
        config[field] = address
    elif field.endswith("s"):
        config[field][0]["source_ip"] = address
    else:
        config[field]["source_ip"] = address
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(CandidateError):
        parse_config(json.dumps(config).encode(), state_dir=tmp_path)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("address", [True, 3232238081])
def test_ingress_refuses_nontext_before_decoder_selection(monkeypatch, address):
    def forbidden(_):
        raise AssertionError("Decoder selection reached for a nontext source")

    monkeypatch.setattr("aikey.aiport_ingest.executable_path", forbidden)
    with pytest.raises(IngressError, match="invalid_stream_source"):
        AiPortIngress(camera_mac="2A1122334455", source_ip=address,
                      ffmpeg_path="/fictional/not-executed")


@pytest.mark.parametrize("address", [True, 3232238081])
def test_provision_refuses_nontext_controller_before_trust_or_identity(tmp_path, monkeypatch, address):
    plan, cert, pin, root = fixture(tmp_path)

    def forbidden(*_):
        raise AssertionError("Trust acquisition reached for a nontext controller")

    monkeypatch.setattr("aikey.aiport_instance_state._trusted_controller", forbidden)
    target = root / "new-port"
    with pytest.raises(InstanceStateError, match="private LAN address"):
        provision_slot(plan, 1, target, controller_ip=address,
                       controller_cert_file=cert, controller_pin=pin)
    assert not target.exists()

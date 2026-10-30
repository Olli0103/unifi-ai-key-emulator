"""Persisted adoption/trust is reported separately from a live connection."""

import json

from aikey.config import atomic_private
from aikey.config_store import ConfigurationStore
from test_control_site import fixture


def test_private_identity_status_has_distinct_profiles_and_no_credential_material(tmp_path):
    from aikey.admin_identity import identity_status
    key, port = fixture(tmp_path)
    raw_key, raw_port = json.loads(key.read_text()), json.loads(port.read_text())
    atomic_private(key.parent / "device-state.json", json.dumps({"schema": 1,
        "mac": raw_key["device"]["mac"], "adopted": True,
        "management": {"token": "fictional-private-management-token"}}))
    atomic_private(port.parent / "aiport-adoption.json", json.dumps({"phase": "adopted",
        "controller_ip": raw_port["controller_ip"], "controller_pin": raw_port["controller_pin"],
        "control_port": 7442}))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    key_result = identity_status("aikey", key.parent, raw_key)
    port_result = identity_status("aiport", port.parent, raw_port)
    assert key_result["persisted_adoption"] is True and port_result["persisted_adoption"] is True
    assert key_result["configured_mac"] != port_result["configured_mac"]
    assert key_result["live_connection_checked"] is False
    assert port_result["saved_pairing_count"] == 2
    assert port_result["controller_trust"]["pin_matches_saved_certificate"] is True
    assert port_result["controller_trust"]["certificate_valid_now"] is True
    assert "fictional-private-management-token" not in json.dumps(key_result)
    assert "management-password" not in json.dumps(key_result)
    assert {p: p.read_bytes() for p in before} == before


def test_mismatched_persisted_identity_is_not_a_valid_adoption(tmp_path):
    from aikey.admin_identity import identity_status
    key, _ = fixture(tmp_path)
    config = json.loads(key.read_text())
    atomic_private(key.parent / "device-state.json", json.dumps({"schema": 1,
        "mac": "020000000077", "adopted": True}))
    result = identity_status("aikey", key.parent, config)
    assert result["persisted_state"] == "binding_mismatch"
    assert result["persisted_adoption"] is None
    assert "020000000077" not in json.dumps(result)


def test_missing_adoption_and_trust_remain_unknown_without_initialization(tmp_path):
    from aikey.admin_identity import identity_status
    key, _ = fixture(tmp_path)
    config = json.loads(key.read_text())
    before = ConfigurationStore(key).snapshot().revision
    result = identity_status("aikey", key.parent, config)
    assert result["persisted_adoption"] is None and result["persisted_state"] == "not_present"
    assert result["controller_trust"]["certificate_valid_now"] is None
    assert not (key.parent / "device-state.json").exists()
    assert ConfigurationStore(key).snapshot().revision == before

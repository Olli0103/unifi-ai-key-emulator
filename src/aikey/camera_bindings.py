"""Versioned identity/order declaration, not native pairing/quality proof."""

import hashlib
import json

from .aiport_ingest import normalize_mac


SCHEMA = "aikey-runtime-pool-binding/1"


def binding_revision(mac, cameras):
    if not isinstance(cameras, (list, tuple)) or not 1 <= len(cameras) <= 5:
        raise ValueError("Invalid runtime camera order")
    normalized = [normalize_mac(c) for c in cameras]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Invalid runtime camera order")
    payload = [SCHEMA, normalize_mac(mac), normalized]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

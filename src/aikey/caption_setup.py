"""Host-bound initial caption policy; no transport, secret read or activation."""

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re

from .config import CONTINUOUS_CAPTION_VERSIONS


@dataclass(frozen=True)
class CaptionSetupBinding:
    controller: str
    api_key_file: str
    web_trust_file: str
    web_cert_file: str

    def __post_init__(self):
        address = ipaddress.ip_address(self.controller)
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in networks):
            raise ValueError("Caption setup needs a private IPv4 inventory binding")
        for value in (self.api_key_file, self.web_trust_file, self.web_cert_file):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 1024
                or not value.isprintable()
                or not Path(value).is_absolute()
                or ".." in Path(value).parts
            ):
                raise ValueError("Caption setup needs fixed absolute inventory file references")


def prerequisites(raw, binding):
    worker = raw.get("worker") or {}
    if (
        not isinstance(binding, CaptionSetupBinding)
        or "continuous" in worker
        or "test_scope" in worker
        or "test_scopes" in worker
        or worker.get("callback_mode") != "enabled"
        or worker.get("request_mp4_exports") is not True
        or not (raw.get("inference") or {}).get("model")
        or raw["controller"].get("host") != binding.controller
        or raw["controller"].get("protect_version") not in CONTINUOUS_CAPTION_VERSIONS
    ):
        raise ValueError("Initial caption setup prerequisites unavailable")


def initial_policy(raw, binding, cameras):
    prerequisites(raw, binding)
    if (
        not isinstance(cameras, list)
        or not 1 <= len(cameras) <= 16
        or any(
            not isinstance(c, dict)
            or set(c) != {"id", "model"}
            or not isinstance(c["id"], str)
            or not re.fullmatch(r"[0-9a-f]{24}", c["id"])
            or not isinstance(c["model"], str)
            or not 1 <= len(c["model"]) <= 128
            or not c["model"].isprintable()
            for c in cameras
        )
        or len({c["id"] for c in cameras}) != len(cameras)
    ):
        raise ValueError("Select distinct initial caption cameras")
    return {
        "enabled": True,
        "api_key_file": binding.api_key_file,
        "web_trust_file": binding.web_trust_file,
        "web_cert_file": binding.web_cert_file,
        "refresh_seconds": 60,
        "camera_ids": sorted(c["id"] for c in cameras),
        "camera_models": sorted({c["model"] for c in cameras}),
    }

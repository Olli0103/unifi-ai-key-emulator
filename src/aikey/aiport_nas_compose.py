"""Generate a private, reviewable Linux NAS Compose manifest for AI Port slots.

This never starts containers, allocates LAN addresses, or pairs cameras. It
requires already provisioned identities so the on-wire MAC, TLS certificate,
and planned IP cannot silently change while moving an instance to the NAS.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import stat

from .aiport_candidate import CandidateError, _private_file, load_config
from .aiport_instance_state import InstanceStateError, provision_slot
from .config import atomic_private


_PARENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")
_NETWORK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}\Z")
_REQUIRED_STATE = ("config.json", "identity.json", "controller-ca.pem",
                   "device.crt", "device.key")


class NasComposeError(ValueError):
    """The plan, state, or NAS network cannot be safely represented."""


def _lan_ip(value: str, network: ipaddress.IPv4Network) -> str:
    if not isinstance(value, str):
        raise NasComposeError("A concrete IPv4 LAN address is required")
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise NasComposeError("A concrete IPv4 LAN address is required") from exc
    if (address not in network or address in (network.network_address,
                                             network.broadcast_address)):
        raise NasComposeError("Every address must be a usable host in the NAS subnet")
    return str(address)


def _verified_state(state_dir: Path, uid: int) -> None:
    if (state_dir.is_symlink() or not state_dir.is_dir()
            or state_dir.stat().st_uid != uid
            or state_dir.stat().st_mode & 0o077):
        raise NasComposeError("Each NAS slot needs an existing, private state directory owned by its user")
    for name in _REQUIRED_STATE:
        path = state_dir / name
        try:
            info = path.lstat()
        except OSError as exc:
            raise NasComposeError("An AI Port identity file is missing") from exc
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_mode & 0o077):
            raise NasComposeError("AI Port identity files must be private and owned by the container user")


def build_nas_compose(plan: dict, state_dirs: dict[int, Path], *,
                      controller_ip: str, controller_pin: str, nas_ip: str,
                      subnet: str, gateway: str, parent: str, image: str,
                      uid: int, gid: int, external_network: str | None = None) -> dict:
    """Build a static-IP Compose model after verifying every selected slot."""
    if (not isinstance(plan, dict) or plan.get("schema") != "aikey-aiport-deployment-plan/2"
            or not isinstance(plan.get("instances"), list)
            or not isinstance(plan.get("ai_key"), dict)
            or not 1 <= len(plan["instances"]) <= 16
            or not isinstance(state_dirs, dict) or not state_dirs
            or any(type(slot) is not int or not 1 <= slot <= len(plan["instances"])
                   for slot in state_dirs)):
        raise NasComposeError("A complete addressed AI Port plan and selected slots are required")
    if (type(uid) is not int or uid <= 0 or type(gid) is not int or gid <= 0
            or not isinstance(parent, str) or not _PARENT.fullmatch(parent)
            or not isinstance(image, str) or not _IMAGE.fullmatch(image)):
        raise NasComposeError("A non-root user, NAS parent interface, and explicit image are required")
    if external_network is not None and (not isinstance(external_network, str)
            or not _NETWORK_NAME.fullmatch(external_network)):
        raise NasComposeError("An existing Docker network needs an explicit valid name")
    try:
        network = ipaddress.IPv4Network(subnet, strict=True)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError,
            TypeError, ValueError) as exc:
        raise NasComposeError("A valid IPv4 NAS LAN subnet is required") from exc
    gateway = _lan_ip(gateway, network)
    nas_ip = _lan_ip(nas_ip, network)
    controller_ip = _lan_ip(controller_ip, network)
    if nas_ip in {gateway, controller_ip}:
        raise NasComposeError("The NAS requires a distinct address from the gateway and Protect")
    key_ip = plan["ai_key"].get("host_ip")
    if key_ip is not None:
        key_ip = _lan_ip(key_ip, network)
    if key_ip in {gateway, nas_ip, controller_ip}:
        raise NasComposeError("The AI Key address conflicts with the NAS network")

    services = {}
    seen_ips = set()
    seen_macs = set()
    for index, item in enumerate(plan["instances"], start=1):
        if (not isinstance(item, dict) or type(item.get("slot")) is not int
                or item["slot"] != index
                or item.get("management_tcp") != 443
                or item.get("source_kind") not in {"protect", "onvif"}
                or not isinstance(item.get("camera_ids"), list)
                or not item["camera_ids"]):
            raise NasComposeError("Invalid AI Port plan slot")
        raw_address = item.get("host_ip")
        if raw_address is None and index not in state_dirs:
            continue
        address = _lan_ip(raw_address, network)
        if address in seen_ips or address in {gateway, nas_ip, controller_ip, key_ip}:
            raise NasComposeError("AI Port LAN addresses must be distinct and reserved")
        seen_ips.add(address)
        if index not in state_dirs:
            continue
        state_dir = Path(state_dirs[index])
        _verified_state(state_dir, uid)
        try:
            config = load_config(state_dir / "config.json",
                                 check_decoder_executable=False)
            provision_slot(plan, index, state_dir, controller_ip=controller_ip,
                           controller_cert_file=state_dir / "controller-ca.pem",
                           controller_pin=controller_pin,
                           firmware_version=config["firmware_version"])
        except (CandidateError, InstanceStateError, OSError, KeyError) as exc:
            raise NasComposeError("AI Port slot identity does not match the plan") from exc
        mac = config["mac"].upper()
        if mac in seen_macs:
            raise NasComposeError("AI Port instance MAC addresses must be distinct")
        seen_macs.add(mac)
        colon_mac = ":".join(mac[offset:offset + 2] for offset in range(0, 12, 2))
        services[f"aiport_slot_{index}"] = {
            "image": image,
            "user": f"{uid}:{gid}",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": ["/tmp:rw,nosuid,noexec,size=64m,mode=1777"],
            "sysctls": {"net.ipv4.ip_unprivileged_port_start": 0},
            "restart": "unless-stopped",
            "stop_grace_period": "20s",
            "command": ["--config", "/state/config.json", "--port", "443"],
            "volumes": [{"type": "bind", "source": str(state_dir.absolute()),
                         "target": "/state", "bind": {"create_host_path": False}}],
            "networks": {"aiport_lan": {"ipv4_address": address,
                                        "mac_address": colon_mac}},
        }
    result = {
        "name": "local-aiport",
        "services": services,
        "networks": {"aiport_lan": {
            "driver": "macvlan", "driver_opts": {"parent": parent},
            "ipam": {"config": [{"subnet": str(network), "gateway": gateway}]},
        }},
    }
    if external_network is not None:
        result["networks"]["aiport_lan"] = {"external": True, "name": external_network}
        result["x-aikey-network-check"] = {
            "name": external_network, "driver": "macvlan", "parent": parent,
            "subnet": str(network), "gateway": gateway,
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a private AI Port NAS Compose manifest")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--slot-state", action="append", required=True,
                        help="Repeat SLOT=PRIVATE_NAS_STATE_DIRECTORY for each NAS slot")
    parser.add_argument("--controller-ip", required=True)
    parser.add_argument("--controller-pin", required=True)
    parser.add_argument("--nas-ip", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--external-network",
                        help="Use an existing macvlan; the reconciler verifies its parent and IPAM")
    parser.add_argument("--image", required=True)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(_private_file(args.plan, 2 * 1024 * 1024))
        states = {}
        for assignment in args.slot_state:
            number, path = assignment.split("=", 1)
            slot = int(number)
            if slot in states or slot <= 0 or not path:
                raise NasComposeError("Duplicate or invalid slot state")
            states[slot] = Path(path)
        result = build_nas_compose(
            plan, states, controller_ip=args.controller_ip, controller_pin=args.controller_pin,
            nas_ip=args.nas_ip, subnet=args.subnet, gateway=args.gateway,
            parent=args.parent, image=args.image, uid=args.uid, gid=args.gid,
            external_network=args.external_network)
        output = args.output
        if output.is_symlink() or (output.exists() and not stat.S_ISREG(output.stat().st_mode)):
            raise NasComposeError("Output must be a regular private file")
        content = json.dumps(result, indent=2) + "\n"
        if output.exists():
            if output.stat().st_mode & 0o077 or output.read_text() != content:
                raise NasComposeError("Existing output differs; inspect it before replacing")
        else:
            if (not output.parent.is_dir() or output.parent.is_symlink()
                    or output.parent.stat().st_mode & 0o077):
                raise NasComposeError("Create a private output directory before generation")
            atomic_private(output, content)
    except (CandidateError, NasComposeError, OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(f"Verified {len(result['services'])} NAS AI Port slot(s); private Compose file ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

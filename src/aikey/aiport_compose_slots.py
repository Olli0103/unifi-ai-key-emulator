"""Append-only edits of the deployed NAS Compose project for new AI Port slots.

The live NAS project is maintained as one Compose file with one service per
AI Port slot. A new slot's service is a copy of an existing (template)
service with exactly four fields changed: the service name, its bind-mounted
state directory, ``ipv4_address`` and ``mac_address``. Existing services stay
byte-for-byte identical, which is verified by re-parsing the result.
:func:`remove_slot_service` is the exact inverse for a rollback.
"""

from __future__ import annotations

import ipaddress
import re


_SERVICE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_MAC = re.compile(r"02(?::[0-9a-f]{2}){5}\Z")
_SOURCE = re.compile(r"/[A-Za-z0-9._/-]{1,200}\Z")


class ComposeSlotError(ValueError):
    """The Compose file cannot be extended safely."""


def colon_mac(mac: str) -> str:
    raw = re.sub(r"[^0-9A-Fa-f]", "", mac).lower()
    if len(raw) != 12:
        raise ComposeSlotError("Invalid AI Port MAC")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def _yaml():
    try:
        import yaml   # optional "rollout" extra; the AI Port images never need it
    except ImportError as exc:
        raise ComposeSlotError("Compose editing needs PyYAML (pip install '.[rollout]')") from exc
    return yaml


def _load(text: str) -> dict:
    yaml = _yaml()
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ComposeSlotError("The Compose file is not valid YAML") from exc
    if not isinstance(value, dict) or not isinstance(value.get("services"), dict):
        raise ComposeSlotError("The Compose file has no services")
    return value


def _network(service: dict) -> tuple[str, dict]:
    networks = service.get("networks")
    if not isinstance(networks, dict) or len(networks) != 1:
        raise ComposeSlotError("Each AI Port service needs exactly one network")
    (name, settings), = networks.items()
    if not isinstance(settings, dict):
        raise ComposeSlotError("The AI Port network needs a static address")
    return name, settings


def _bind_source(service: dict) -> str:
    volumes = service.get("volumes")
    if (not isinstance(volumes, list) or len(volumes) != 1
            or not isinstance(volumes[0], dict) or volumes[0].get("target") != "/state"):
        raise ComposeSlotError("Each AI Port service needs one /state bind mount")
    return volumes[0].get("source")


def _block(text: str, service: str) -> str:
    match = re.search(rf"(?m)^  {re.escape(service)}:\n(?:(?:    .*|\s*)\n)*", text)
    if match is None:
        raise ComposeSlotError("The template service block was not found")
    return match.group(0).rstrip("\n") + "\n"


def slot_service(text: str, *, template: str, service: str, source: str,
                 ipv4: str, mac: str) -> str:
    """The exact YAML block the new service would add (for review)."""
    compose = _load(text)
    services = compose["services"]
    if template not in services or not _SERVICE.fullmatch(service):
        raise ComposeSlotError("Unknown template or invalid service name")
    if not _SOURCE.fullmatch(source) or ".." in source:
        raise ComposeSlotError("Invalid NAS state directory")
    mac = colon_mac(mac)
    if not _MAC.fullmatch(mac):
        raise ComposeSlotError("A locally administered AI Port MAC is required")
    ipv4 = str(ipaddress.IPv4Address(ipv4))
    base = services[template]
    _, net = _network(base)
    old = {"name": template, "source": _bind_source(base),
           "ipv4": net.get("ipv4_address"), "mac": net.get("mac_address")}
    if not all(isinstance(value, str) for value in old.values()):
        raise ComposeSlotError("The template service lacks a static address")
    block = _block(text, template)
    replacements = ((f"  {old['name']}:\n", f"  {service}:\n"),
                    (f"source: {old['source']}\n", f"source: {source}\n"),
                    (f"ipv4_address: {old['ipv4']}\n", f"ipv4_address: {ipv4}\n"),
                    (f"mac_address: {old['mac']}\n", f"mac_address: {mac}\n"))
    for before, after in replacements:
        if block.count(before) != 1:
            raise ComposeSlotError("The template service is not in the expected shape")
        block = block.replace(before, after)
    return block


def add_slot_service(text: str, *, template: str, service: str, source: str,
                     ipv4: str, mac: str) -> str:
    """Return the Compose text with the new service appended (idempotent)."""
    block = slot_service(text, template=template, service=service, source=source,
                         ipv4=ipv4, mac=mac)
    compose = _load(text)
    services = compose["services"]
    expected = _load("services:\n" + block)["services"][service]
    if service in services:
        if services[service] == expected:
            return text                                   # already applied
        raise ComposeSlotError("A different service with this name already exists")
    for name, item in services.items():
        _, net = _network(item)
        if (net.get("ipv4_address") == expected["networks"][next(iter(expected["networks"]))][
                "ipv4_address"]
                or str(net.get("mac_address", "")).lower() == colon_mac(mac)
                or _bind_source(item) == source):
            raise ComposeSlotError("The address, MAC or state directory is already in use")
    last = list(services)[-1]
    anchor = _block(text, last)
    position = text.index(anchor) + len(anchor)
    result = text[:position] + block + text[position:]
    parsed = _load(result)
    if ({k: v for k, v in parsed["services"].items() if k != service} != services
            or parsed["services"].get(service) != expected
            or {k: v for k, v in parsed.items() if k != "services"}
            != {k: v for k, v in compose.items() if k != "services"}):
        raise ComposeSlotError("Appending the service would change the existing project")
    return result


def remove_slot_service(text: str, *, block: str) -> str:
    """Remove exactly a previously added service block (rollback)."""
    if text.count(block) != 1:
        raise ComposeSlotError("The added service changed; restore it by hand")
    service = block.split(":", 1)[0].strip()
    before = _load(text)
    result = text.replace(block, "", 1)
    after = _load(result)
    if (service in after["services"]
            or {k: v for k, v in before["services"].items() if k != service}
            != after["services"]):
        raise ComposeSlotError("Removing the service would change the existing project")
    return result

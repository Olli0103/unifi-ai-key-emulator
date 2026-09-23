"""All-or-nothing host HTTPS listeners for addressed AI Port instances.

The relay forwards TLS bytes without reading HTTP. It verifies every local
candidate certificate before opening port 443, accepts only the controller's
source address, and drops root privileges after binding. It never adopts or
pairs cameras and cannot invent missing LAN addresses.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import ssl

from .aiport_candidate import CandidateError, _private_file, _private_ipv4, load_config
from .aiport_instance_state import InstanceStateError, provision_slot
from .aiport_relay import BoundedRelay, RelayError, _ipv4


_MAX_INSTANCES = 16


class HostRelayError(ValueError):
    """A host listener set is unsafe or cannot reach its pinned candidate."""


@dataclass(frozen=True)
class Endpoint:
    slot: int
    host_ip: str
    cert_file: Path
    upstream_port: int = 8443


def endpoints_from_plan(plan: dict, state_dirs: dict[int, Path], *,
                        controller_ip: str, controller_pin: str) -> tuple[Endpoint, ...]:
    """Verify complete, addressed instance state without creating or moving it."""
    if (not isinstance(plan, dict) or plan.get("schema") != "aikey-aiport-deployment-plan/2"
            or not isinstance(plan.get("instances"), list)
            or not 1 <= len(plan["instances"]) <= _MAX_INSTANCES
            or not isinstance(state_dirs, dict)
            or set(state_dirs) != set(range(1, len(plan["instances"]) + 1))):
        raise HostRelayError("A complete addressed AI Port plan and state map are required")
    try:
        controller_ip = _private_ipv4(controller_ip)
    except CandidateError as exc:
        raise HostRelayError("Controller requires a private LAN address") from exc
    endpoints = []
    addresses = set()
    for slot, item in enumerate(plan["instances"], start=1):
        state_dir = Path(state_dirs[slot])
        required = ("config.json", "identity.json", "controller-ca.pem",
                    "device.crt", "device.key")
        if (not state_dir.is_dir() or state_dir.is_symlink()
                or any(not (state_dir / name).is_file() for name in required)):
            raise HostRelayError("AI Port instance state is missing or unsafe")
        try:
            config = load_config(state_dir / "config.json")
            verified = provision_slot(
                plan, slot, state_dir, controller_ip=controller_ip,
                controller_cert_file=state_dir / "controller-ca.pem",
                controller_pin=controller_pin,
                firmware_version=config["firmware_version"])
            address = verified["host_ip"]
        except (CandidateError, InstanceStateError, OSError) as exc:
            raise HostRelayError("AI Port instance identity does not match its slot") from exc
        if (not isinstance(item, dict) or item.get("management_tcp") != 443
                or address in addresses or address == controller_ip):
            raise HostRelayError("AI Port host addresses and port 443 must be distinct")
        addresses.add(address)
        endpoints.append(Endpoint(slot, address, state_dir / "device.crt"))
    return tuple(endpoints)


class HostRelayGroup:
    """Bind every verified endpoint, or leave none of the new listeners open."""

    def __init__(self, endpoints: tuple[Endpoint, ...], *, controller_ip: str,
                 listen_port: int = 443, allow_loopback: bool = False):
        if (not isinstance(endpoints, tuple) or not 1 <= len(endpoints) <= _MAX_INSTANCES
                or type(listen_port) is not int or not 0 <= listen_port <= 65535):
            raise HostRelayError("Invalid AI Port relay listener set")
        try:
            self.controller_ip = _ipv4(controller_ip, allow_loopback=allow_loopback)
            addresses = []
            for endpoint in endpoints:
                if (not isinstance(endpoint, Endpoint) or type(endpoint.slot) is not int
                        or endpoint.slot <= 0):
                    raise HostRelayError("Invalid AI Port relay endpoint")
                address = _ipv4(endpoint.host_ip, allow_loopback=allow_loopback)
                if address == self.controller_ip and not allow_loopback:
                    raise HostRelayError("Controller and AI Port require distinct addresses")
                if (type(endpoint.upstream_port) is not int
                        or not 1 <= endpoint.upstream_port <= 65535
                        or endpoint.upstream_port == listen_port):
                    raise HostRelayError("Invalid AI Port upstream port")
                addresses.append(address)
            if len(set(addresses)) != len(addresses):
                raise HostRelayError("AI Port host addresses must be distinct")
            if len({endpoint.slot for endpoint in endpoints}) != len(endpoints):
                raise HostRelayError("AI Port slots must be distinct")
        except RelayError as exc:
            raise HostRelayError("Relay requires private LAN addresses") from exc
        self.endpoints = endpoints
        self.listen_port = listen_port
        self.allow_loopback = allow_loopback
        self._relays: list[BoundedRelay] = []

    async def preflight(self) -> None:
        """Authenticate each already running local TLS endpoint before bind."""
        for endpoint in self.endpoints:
            await self._verify_endpoint(endpoint)

    @staticmethod
    async def _verify_endpoint(endpoint: Endpoint) -> None:
        try:
            pem = _private_file(endpoint.cert_file, 16384)
            expected = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
            context = ssl.create_default_context(cafile=str(endpoint.cert_file))
            context.check_hostname = False
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            _, writer = await asyncio.wait_for(asyncio.open_connection(
                endpoint.host_ip, endpoint.upstream_port, ssl=context,
                server_hostname=endpoint.host_ip), timeout=5)
            try:
                tls = writer.get_extra_info("ssl_object")
                peer = tls.getpeercert(binary_form=True) if tls is not None else None
                if not peer or not hmac.compare_digest(hashlib.sha256(peer).digest(),
                                                       hashlib.sha256(expected).digest()):
                    raise HostRelayError("AI Port upstream certificate changed")
            finally:
                writer.close()
                await writer.wait_closed()
        except (CandidateError, OSError, ssl.SSLError, TimeoutError,
                UnicodeError, ValueError) as exc:
            raise HostRelayError("AI Port upstream TLS preflight failed") from exc

    async def start(self) -> None:
        if self._relays:
            return
        await self.preflight()
        started = []
        try:
            for endpoint in self.endpoints:
                relay = BoundedRelay(
                    listen_ip=endpoint.host_ip, listen_port=self.listen_port,
                    upstream_port=endpoint.upstream_port,
                    allowed_source_ip=self.controller_ip,
                    allow_loopback=self.allow_loopback,
                    upstream_check=lambda endpoint=endpoint: self._verify_endpoint(endpoint))
                await relay.start()
                started.append(relay)
        except BaseException as exc:
            for relay in reversed(started):
                await relay.stop()
            if isinstance(exc, (OSError, RelayError)):
                raise HostRelayError(
                    "AI Port host port 443 is unavailable; no new listeners remain") from None
            raise
        self._relays = started

    async def stop(self) -> dict[str, int]:
        counters = {"accepted": sum(relay.accepted for relay in self._relays),
                    "rejected": sum(relay.rejected for relay in self._relays)}
        for relay in reversed(self._relays):
            await relay.stop()
        self._relays = []
        return counters


async def _run(args) -> None:
    plan = json.loads(_private_file(args.plan, 2 * 1024 * 1024))
    state_dirs = {}
    for assignment in args.slot_state:
        try:
            number, path = assignment.split("=", 1)
            slot = int(number)
        except (ValueError, AttributeError) as exc:
            raise HostRelayError("Slot state must use SLOT=PRIVATE_DIRECTORY") from exc
        if slot in state_dirs or slot <= 0 or not path:
            raise HostRelayError("Duplicate or invalid slot state")
        state_dirs[slot] = Path(path)
    endpoints = endpoints_from_plan(
        plan, state_dirs, controller_ip=args.controller_ip,
        controller_pin=args.controller_pin)
    relay = HostRelayGroup(endpoints, controller_ip=args.controller_ip)
    if args.preflight_only:
        await relay.preflight()
        print(f"AI Port host relay preflight passed for {len(endpoints)} instance(s)", flush=True)
        return
    await relay.start()
    try:
        if os.geteuid() == 0:
            os.setgroups([])
            os.setgid(args.drop_gid)
            os.setuid(args.drop_uid)
        elif os.geteuid() != args.drop_uid or os.getegid() != args.drop_gid:
            raise HostRelayError("Relay must drop to the requested non-root identity")
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, done.set)
        print("AI Port host relay active", flush=True)
        try:
            if args.seconds:
                await asyncio.wait_for(done.wait(), timeout=args.seconds)
            else:
                await done.wait()
        except TimeoutError:
            pass
    finally:
        counters = await relay.stop()
        print("AI Port host relay stopped; "
              f"accepted={counters['accepted']} rejected={counters['rejected']}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward AI Port HTTPS 443 for all addressed instances")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--slot-state", action="append", required=True)
    parser.add_argument("--controller-ip", required=True)
    parser.add_argument("--controller-pin", required=True)
    parser.add_argument("--drop-uid", type=int)
    parser.add_argument("--drop-gid", type=int)
    parser.add_argument("--preflight-only", action="store_true",
                        help="verify private state and all upstream certificates without binding")
    parser.add_argument("--seconds", type=int, default=600,
                        help="0 waits until SIGTERM; otherwise 1 to 86400")
    args = parser.parse_args(argv)
    if (not args.preflight_only and (args.drop_uid is None or args.drop_uid <= 0
                                     or args.drop_gid is None or args.drop_gid <= 0)
            or not 0 <= args.seconds <= 86400):
        parser.error("Use a non-root identity and a 0-86400 second lifetime")
    try:
        asyncio.run(_run(args))
    except (CandidateError, HostRelayError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Foreground macOS discovery companion for a pinned local container runtime.

Only configuration and the public device certificate are read. This process
never loads management credentials, API keys or the device private key.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import signal
import ssl
import sys
import time

import aiohttp

from .config import atomic_private, load_config
from .discovery import DISCOVERY_PORT, DiscoveryService, build_discovery_response
from .protocol import ContractError


HEALTH_TTL = 10.0
POLL_INTERVAL = 2.0
MAX_RESPONSE_BYTES = 64 * 1024


class HostDiscoveryCompanion:
    """Answer controller discovery queries while the pinned runtime is healthy."""

    def __init__(self, config: dict, state_dir: Path, *, allow_macos_host: bool = False,
                 clock=time.monotonic):
        self.config = deepcopy(config)
        self.state_dir = Path(state_dir).resolve()
        self.status_path = self.state_dir / "host-discovery-status.json"
        self.clock = clock
        runtime, device = self.config["runtime"], self.config["device"]
        mode = runtime.get("mode")
        if mode not in {"lab", "device"}:
            raise ValueError("Host discovery requires device or loopback lab mode")
        device_ip = ipaddress.IPv4Address(device["ip"])
        controller_ip = ipaddress.IPv4Address(self.config["controller"]["host"])
        for address in (device_ip, controller_ip):
            if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
                raise ValueError("Host discovery requires concrete unicast IPv4 addresses")
        if mode == "lab":
            if not device_ip.is_loopback or not controller_ip.is_loopback:
                raise ValueError("Host discovery lab endpoints must be loopback")
        elif (sys.platform != "darwin" or allow_macos_host is not True
              or device_ip.is_loopback or controller_ip.is_loopback):
            raise ValueError("LAN host discovery requires explicit macOS host enablement and LAN addresses")
        port = runtime.get("https_port", 8080)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid runtime HTTPS port")
        self.base_url = f"https://{device_ip}:{port}"
        # Trust only the exact leaf certificate already provisioned for this runtime.
        # Its advertised host address need not appear in the certificate SAN.
        cert_path = self.state_dir / "device.crt"
        if not cert_path.is_file() or cert_path.stat().st_size > 64 * 1024:
            raise ValueError("A bounded local device certificate is required")
        der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="ascii"))
        self.fingerprint = aiohttp.Fingerprint(hashlib.sha256(der).digest())
        self._info: dict | None = None
        self._adopted = False
        self._last_success: float | None = None
        self._healthy = False
        self._last_error: str | None = None
        self._successes = 0
        self._failures = 0
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        discovery = self.config.get("discovery", {})
        self.config["discovery"] = {
            "enabled": True,
            "bind": str(device_ip) if mode == "lab" else "0.0.0.0",
            "port": discovery.get("port", 0) if mode == "lab" else DISCOVERY_PORT,
            "multicast": mode == "device", "interface_ip": str(device_ip),
            "allowed_controller_ips": [str(controller_ip)],
        }
        self.discovery = DiscoveryService(self.config, self._get_info, lambda: self._adopted,
            allow_macos_host=allow_macos_host, ready_provider=lambda: self.health_fresh)

    @property
    def health_fresh(self) -> bool:
        return (self._healthy and self._last_success is not None
                and 0 <= self.clock() - self._last_success < HEALTH_TTL)

    @property
    def status(self) -> dict:
        age = None if self._last_success is None else max(0, self.clock() - self._last_success)
        return {"service": "local-aikey-host-discovery", "running": self._running,
                "health_fresh": self.health_fresh,
                "last_health_age_s": None if age is None else round(age, 3),
                "health_successes": self._successes, "health_failures": self._failures,
                "last_error": self._last_error,
                "responding": self.discovery.transport is not None and self.health_fresh,
                "discovery": self.discovery.status}

    def _get_info(self) -> dict:
        if not self.health_fresh or self._info is None:
            raise ContractError("Runtime health is unavailable or stale")
        return self._info

    def _write_status(self) -> None:
        atomic_private(self.status_path, json.dumps(self.status, allow_nan=False) + "\n")

    async def _fetch(self, path: str) -> dict:
        assert self._session is not None
        async with self._session.get(self.base_url + path, allow_redirects=False) as response:
            if response.status != 200:
                raise ContractError("Runtime health request failed")
            if response.content_type != "application/json":
                raise ContractError("Runtime health requires JSON")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ContractError("Runtime health response exceeds its limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ContractError("Runtime health requires an object")
            return result

    async def refresh(self) -> bool:
        """Refresh both read-only endpoints, invalidating the cache on any failure."""
        started = self.clock()
        try:
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=self.fingerprint, limit=2),
                    timeout=aiohttp.ClientTimeout(total=3), trust_env=False, auto_decompress=False)
            info = await self._fetch("/api/info")
            health = await self._fetch("/healthz")
            expected_mac = self.config["device"]["mac"].replace(":", "").replace("-", "").upper()
            if info.get("mac") != expected_mac:
                raise ContractError("Runtime identity does not match configuration")
            device_status = health.get("device")
            if (health.get("service") != "local-aikey" or not isinstance(device_status, dict)
                    or type(device_status.get("adopted")) is not bool):
                raise ContractError("Invalid runtime health status")
            adopted = device_status["adopted"]
            build_discovery_response(info, ip=self.config["device"]["ip"],
                hostname=self.config["device"].get("name", "local-aikey"), adopted=adopted)
            if not 0 <= self.clock() - started < HEALTH_TTL:
                raise ContractError("Runtime health response is already stale")
            self._info, self._adopted = info, adopted
            self._last_success = started
            self._healthy = True
            self._last_error = None
            self._successes += 1
            return True
        except asyncio.CancelledError:
            self._healthy = False
            raise
        except Exception as exc:
            self._healthy = False
            self._failures += 1
            self._last_error = type(exc).__name__
            return False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            if await self.refresh():
                await self.discovery.start()
            self._write_status()
            self._task = asyncio.create_task(self._poll(), name="aikey-host-discovery")
        except BaseException:
            await self.stop()
            raise

    async def _poll(self) -> None:
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL)
                if await self.refresh():
                    try:
                        await self.discovery.start()
                    except Exception as exc:
                        self._healthy = False
                        self._last_error = type(exc).__name__
                self._write_status()
        finally:
            self._healthy = False

    async def stop(self) -> None:
        self._running = False
        self._healthy = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._last_error = type(exc).__name__
            self._task = None
        await self.discovery.stop()
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._write_status()


async def _run(config: dict, state_dir: Path, allow_macos_host: bool) -> None:
    service = HostDiscoveryCompanion(config, state_dir, allow_macos_host=allow_macos_host)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    signal_waiter = None
    try:
        await service.start()
        signal_waiter = asyncio.create_task(stopped.wait())
        done, _ = await asyncio.wait((signal_waiter, service._task), return_when=asyncio.FIRST_COMPLETED)
        if service._task in done:
            await service._task
    finally:
        if signal_waiter is not None:
            signal_waiter.cancel()
            await asyncio.gather(signal_waiter, return_exceptions=True)
        await service.stop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pinned, read-only macOS host discovery companion")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True, help="Host directory containing device.crt")
    parser.add_argument("--allow-macos-host", action="store_true", help="Explicitly enable LAN UDP discovery on this Mac")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_run(load_config(args.config), args.state_dir, args.allow_macos_host))
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error("Host discovery stopped (%s)", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Isolated, unadopted AI Port candidate for native interface discovery.

This service has no camera access, adoption credentials or detection path. It
holds a pinned outbound device connection and a TLS management listener whose
adoption route deliberately rejects requests after recording only their shape.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import time

import aiohttp
from aiohttp import web

from .device import VerifiedConnector


_MAC = re.compile(r"[0-9A-Fa-f]{12}\Z")
_PIN = re.compile(r"[0-9A-Fa-f]{64}\Z")
_VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z")
_MAX_MANAGE = 8192
_ALLOWED_TOP_LEVEL = frozenset(("username", "password", "mgmt", "hosts", "protocol", "mode"))
_ALLOWED_MGMT = frozenset(("token", "hosts", "protocol", "mode", "nvr"))


class CandidateError(ValueError):
    """Unsafe or incomplete isolated AI Port candidate configuration."""


def _private_file(path: Path, max_size: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > max_size:
                raise CandidateError("Candidate state file must be private and size-bounded")
            return handle.read(max_size + 1)
    except OSError as exc:
        raise CandidateError("Candidate state file is unavailable") from exc


def _private_ipv4(value: object) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise CandidateError("Candidate requires an explicit IPv4 address") from exc
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise CandidateError("Candidate requires a private LAN IPv4 address")
    return str(address)


def load_config(path: Path) -> dict:
    raw = _private_file(path, 4096)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CandidateError("Invalid candidate configuration JSON") from exc
    required = {"controller_ip", "device_ip", "mac", "controller_pin", "firmware_version"}
    if not isinstance(value, dict) or set(value) != required:
        raise CandidateError("Candidate configuration fields do not match the isolated profile")
    value["controller_ip"] = _private_ipv4(value["controller_ip"])
    value["device_ip"] = _private_ipv4(value["device_ip"])
    mac = value["mac"]
    if not isinstance(mac, str) or not _MAC.fullmatch(mac) or int(mac[:2], 16) & 3 != 2:
        raise CandidateError("Candidate requires a distinct locally administered unicast MAC")
    value["mac"] = mac.upper()
    pin = value["controller_pin"]
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        raise CandidateError("Candidate requires a controller SHA-256 certificate pin")
    value["controller_pin"] = pin.lower()
    version = value["firmware_version"]
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise CandidateError("Invalid candidate firmware version")
    for name in ("device.crt", "device.key", "controller-ca.pem"):
        _private_file(path.parent / name, 16384)
    return value


def _object_shape(raw: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        body = json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        return {"valid_json_object": False}
    if not isinstance(body, dict):
        return {"valid_json_object": False}
    mgmt = body.get("mgmt")
    return {"valid_json_object": True,
            "recognized_fields": sorted(set(body) & _ALLOWED_TOP_LEVEL),
            "other_field_count": len(set(body) - _ALLOWED_TOP_LEVEL),
            "mgmt_recognized_fields": sorted(set(mgmt) & _ALLOWED_MGMT) if isinstance(mgmt, dict) else [],
            "mgmt_other_field_count": len(set(mgmt) - _ALLOWED_MGMT) if isinstance(mgmt, dict) else 0}


class CandidateService:
    def __init__(self, config: dict, state_dir: Path, *, control_port: int = 7442):
        self.config = config
        self.state_dir = Path(state_dir)
        self.control_port = control_port
        self.runner: web.AppRunner | None = None
        self.task: asyncio.Task | None = None
        self.connected = False
        self.upgrades = 0
        self.last_result: str | None = None
        self.manage_requests = 0
        self.last_manage_shape: dict | None = None
        self.ws_binary_frames = 0
        self.ws_text_frames = 0
        self.ws_last_frame_bytes: int | None = None
        self.started = time.monotonic()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=_MAX_MANAGE)
        app.router.add_get("/healthz", self._health)
        app.router.add_post("/api/1.2/manage", self._manage)
        return app

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"service": "aiport-candidate", "adopted": False,
            "control_connected": self.connected, "websocket_upgrades": self.upgrades,
            "last_result": self.last_result, "manage_requests": self.manage_requests,
            "last_manage_shape": self.last_manage_shape,
            "ws_binary_frames": self.ws_binary_frames,
            "ws_text_frames": self.ws_text_frames,
            "ws_last_frame_bytes": self.ws_last_frame_bytes,
            "uptime_seconds": int(time.monotonic() - self.started)})

    async def _manage(self, request: web.Request) -> web.Response:
        self.manage_requests += 1
        if not request.secure:
            return web.json_response({"error": "HTTPS required"}, status=400)
        if request.content_type != "application/json":
            return web.json_response({"error": "JSON required"}, status=415)
        try:
            raw = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"error": "Request too large"}, status=413)
        self.last_manage_shape = _object_shape(raw)
        return web.json_response({"error": "Adoption is not enabled on this candidate"}, status=501)

    def _client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=str(self.state_dir / "controller-ca.pem"))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.load_cert_chain(str(self.state_dir / "device.crt"), str(self.state_dir / "device.key"))
        return context

    def _server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.state_dir / "device.crt"), str(self.state_dir / "device.key"))
        return context

    async def start(self, *, bind: str = "0.0.0.0", port: int = 8443):
        if self.runner is not None:
            return
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, bind, port, ssl_context=self._server_context()).start()
            self.task = asyncio.create_task(self._connect_loop(), name="aiport-candidate-control")
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    async def _connect_loop(self):
        connector = VerifiedConnector(ssl_context=self._client_context(),
                                      expected_fingerprint=self.config["controller_pin"])
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
            while True:
                headers = {"Camera-MAC": self.config["mac"], "Camera-IP": self.config["device_ip"],
                           "Camera-Model": "0xa5f1", "Camera-Firmware": self.config["firmware_version"],
                           "Adopted": "false"}
                try:
                    async with session.ws_connect(
                        f"wss://{self.config['controller_ip']}:{self.control_port}/camera/1.0/ws",
                        protocols=["secure_transfer"], headers=headers,
                        heartbeat=30, max_msg_size=64 * 1024,
                    ) as ws:
                        if ws.protocol not in (None, "secure_transfer"):
                            self.last_result = "websocket_protocol_mismatch"
                            await ws.close()
                        else:
                            self.upgrades += 1
                            self.connected = True
                            self.last_result = "websocket_101"
                            async for message in ws:
                                if message.type == aiohttp.WSMsgType.BINARY:
                                    self.ws_binary_frames += 1
                                    self.ws_last_frame_bytes = len(message.data)
                                elif message.type == aiohttp.WSMsgType.TEXT:
                                    self.ws_text_frames += 1
                                    self.ws_last_frame_bytes = len(message.data.encode("utf-8"))
                                if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                                    aiohttp.WSMsgType.ERROR):
                                    break
                            self.last_result = "websocket_closed"
                except (aiohttp.ClientError, TimeoutError, ssl.SSLError) as exc:
                    self.last_result = type(exc).__name__
                finally:
                    self.connected = False
                await asyncio.sleep(5)


async def _serve(path: Path, bind: str, port: int):
    config = load_config(path)
    service = CandidateService(config, path.parent)
    await service.start(bind=bind, port=port)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, done.set)
    try:
        await done.wait()
    finally:
        await service.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an unadopted AI Port candidate with no camera access")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args(argv)
    if args.bind not in ("0.0.0.0", "127.0.0.1") or not 0 <= args.port <= 65535:
        parser.error("Invalid candidate bind address or port")
    try:
        asyncio.run(_serve(args.config, args.bind, args.port))
    except (CandidateError, OSError, ssl.SSLError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

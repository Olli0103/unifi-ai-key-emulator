"""Bounded RTSP ingress for one explicitly authorized AI Port camera.

The controller supplies a stream alias, but it cannot choose an arbitrary
network destination. A private operator policy fixes both camera identity and
source address. Frames stay in memory and this module sends no detections.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path
import re
import time


_MAC = re.compile(r"(?:[0-9A-Fa-f]{12}|(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\Z")
_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_FRAME = 1024 * 1024


class IngressError(ValueError):
    """A safe, non-secret reason for refusing a stream-control command."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_mac(value: object) -> str:
    if not isinstance(value, str) or not _MAC.fullmatch(value):
        raise IngressError("invalid_device_id")
    return value.replace(":", "").upper()


def private_source_ip(value: object) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise IngressError("invalid_stream_source") from exc
    if (not address.is_private or address.is_loopback or address.is_link_local
            or address.is_unspecified or address.is_multicast):
        raise IngressError("invalid_stream_source")
    return str(address)


def executable_path(value: object) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise IngressError("invalid_decoder")
    path = Path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise IngressError("invalid_decoder")
    return str(path)


@dataclass(frozen=True)
class StreamSpec:
    device_id: str
    ip: str
    alias: str
    width: int
    height: int
    fps: float
    points: int

    @property
    def url(self) -> str:
        return f"rtsp://{self.ip}:7447/{self.alias}"


def _stream_spec(payload: object, *, camera_mac: str, source_ip: str) -> StreamSpec:
    if not isinstance(payload, dict) or set(payload) != {
        "streaming", "ip", "port", "uri", "deviceID", "width", "height", "fps"
    } or payload.get("streaming") is not True:
        raise IngressError("invalid_stream_command")
    device_id = normalize_mac(payload["deviceID"])
    if device_id != camera_mac:
        raise IngressError("camera_not_authorized")
    if payload["ip"] != source_ip or payload["port"] not in ("7447", 7447):
        raise IngressError("stream_source_not_authorized")
    alias = payload["uri"]
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
        raise IngressError("invalid_stream_alias")
    width, height, fps = payload["width"], payload["height"], payload["fps"]
    if (type(width) is not int or type(height) is not int
            or width < 16 or width > 8192 or height < 16 or height > 4320
            or type(fps) not in (int, float) or not math.isfinite(fps)
            or fps < 1 or fps > 120):
        raise IngressError("invalid_stream_dimensions")
    pixels = width * height
    points = 2 if pixels <= 1920 * 1080 else 3 if pixels <= 2560 * 1440 else 5
    return StreamSpec(device_id, source_ip, alias, width, height, float(fps), points)


class _Session:
    def __init__(self, spec: StreamSpec, ffmpeg_path: str):
        self.spec = spec
        self.ffmpeg_path = ffmpeg_path
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.first_frame = asyncio.Event()
        self.frame_count = 0
        self.latest_frame: bytes | None = None
        self.last_frame_at = 0.0
        self.failure: str | None = None

    @property
    def healthy(self) -> bool:
        return (self.process is not None and self.process.returncode is None
                and self.failure is None and self.frame_count > 0
                and time.monotonic() - self.last_frame_at < 15)

    async def start(self, timeout: float) -> None:
        # The alias and destination have already passed exact policy checks.
        # stderr is discarded because ffmpeg may print the private stream URL.
        self.process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-rw_timeout", "5000000", "-i", self.spec.url,
            "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", "fps=1,scale=320:-2", "-threads", "1", "-f", "image2pipe",
            "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=_MAX_FRAME + 2,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        self.reader = asyncio.create_task(self._read_frames(), name="aiport-rtsp-frames")
        try:
            await asyncio.wait_for(self.first_frame.wait(), timeout=timeout)
        except TimeoutError as exc:
            await self.close()
            raise IngressError("stream_start_timeout") from exc
        if not self.healthy:
            reason = self.failure or "stream_unavailable"
            await self.close()
            raise IngressError(reason)

    async def _read_frames(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                frame = await self.process.stdout.readuntil(b"\xff\xd9")
                if not frame.startswith(b"\xff\xd8") or len(frame) > _MAX_FRAME:
                    self.failure = "invalid_decoded_frame"
                    return
                self.latest_frame = frame
                self.frame_count += 1
                self.last_frame_at = time.monotonic()
                self.first_frame.set()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            self.failure = "stream_ended"
        except asyncio.CancelledError:
            raise
        finally:
            self.first_frame.set()

    async def close(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.reader = None
        self.process = None
        self.latest_frame = None


class AiPortIngress:
    """Start only an allowed RTSP stream and confirm a decoded frame first."""

    def __init__(self, *, camera_mac: str, source_ip: str, ffmpeg_path: str,
                 start_timeout: float = 7):
        self.camera_mac = normalize_mac(camera_mac)
        self.source_ip = private_source_ip(source_ip)
        self.ffmpeg_path = executable_path(ffmpeg_path)
        if not 0 < start_timeout < 10:
            raise IngressError("invalid_start_timeout")
        self.start_timeout = start_timeout
        self._session: _Session | None = None
        self._lock = asyncio.Lock()
        self.total_frames_decoded = 0

    async def control(self, payload: object) -> dict:
        if not isinstance(payload, dict) or "streaming" not in payload:
            raise IngressError("invalid_stream_command")
        if payload["streaming"] is False:
            if set(payload) != {"streaming", "deviceID"}:
                raise IngressError("invalid_stream_command")
            if normalize_mac(payload["deviceID"]) != self.camera_mac:
                raise IngressError("camera_not_authorized")
            async with self._lock:
                await self._close_locked()
            return {"status": "stopped", "usedPoints": 0}
        spec = _stream_spec(payload, camera_mac=self.camera_mac, source_ip=self.source_ip)
        async with self._lock:
            if self._session is not None:
                if self._session.spec == spec and self._session.healthy:
                    return {"status": "started", "usedPoints": spec.points}
                await self._close_locked()
            session = _Session(spec, self.ffmpeg_path)
            try:
                await session.start(self.start_timeout)
            except (OSError, IngressError) as exc:
                await session.close()
                if isinstance(exc, IngressError):
                    raise
                raise IngressError("stream_decoder_unavailable") from exc
            self._session = session
            return {"status": "started", "usedPoints": spec.points}

    def list_streams(self) -> list[dict]:
        session = self._session
        if session is None or not session.healthy:
            return []
        return [{"deviceID": session.spec.device_id, "points": session.spec.points}]

    def latest_frame(self) -> bytes | None:
        session = self._session
        return session.latest_frame if session is not None and session.healthy else None

    @property
    def frame_count(self) -> int:
        return self._session.frame_count if self._session is not None else 0

    async def _close_locked(self) -> None:
        if self._session is not None:
            await self._session.close()
            self.total_frames_decoded += self._session.frame_count
            self._session = None

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

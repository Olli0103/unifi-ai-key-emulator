"""Bounded RTSP ingress for one explicitly authorized AI Port camera.

The controller supplies a stream alias, but it cannot choose an arbitrary
network destination. A private operator policy fixes both camera identity and
source address. Frames stay in memory and this module sends no detections.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path
import re
import time
from typing import Awaitable, Callable


_MAC = re.compile(r"(?:[0-9A-Fa-f]{12}|(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\Z")
_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_FRAME = 1024 * 1024
_MAX_DECODER_DIAGNOSTIC = 8192
_DECODER_MARKERS = (
    (b"error opening input", "input_open_failed"),
    (b"does not contain any stream", "no_video_stream"),
    (b"could not find codec parameters", "codec_parameters_missing"),
    (b"error while filtering", "filter_failed"),
    (b"option not found", "decoder_option_missing"),
    (b"connection timed out", "connection_timed_out"),
    (b"input/output error", "io_error"),
    (b"end of file", "unexpected_eof"),
)
_DECODER_TERMS = frozenset({
    "address", "argument", "authorization", "bad", "codec", "connection",
    "contains", "data", "decode", "decoder", "demuxer", "denied", "describe", "device",
    "encoder", "error", "failed", "file", "filter", "format", "found", "frame",
    "handshake", "h264", "hevc", "input", "invalid", "mjpeg", "muxer",
    "matches", "network", "no", "not", "open", "opening", "option", "output",
    "parse", "permission", "play",
    "protocol", "refused", "resource", "rtsp", "rtp", "scale", "server",
    "session", "setup", "stream", "streams", "tcp", "timed", "timeout",
    "transport", "unauthorized", "unavailable", "unsupported",
})


class IngressError(ValueError):
    """A safe, non-secret reason for refusing a stream-control command."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _decoder_failure(stderr: bytes) -> str:
    """Reduce private FFmpeg output to a fixed, non-secret failure code."""
    output = stderr.lower()
    if b"401 unauthorized" in output or b"403 forbidden" in output:
        return "rtsp_access_denied"
    if b"404 not found" in output:
        return "rtsp_stream_not_found"
    if b"connection refused" in output or b"network is unreachable" in output:
        return "rtsp_connect_failed"
    if b"400 bad request" in output or b"protocol not found" in output:
        return "rtsp_protocol_rejected"
    if b"option rw_timeout not found" in output:
        return "decoder_option_missing"
    if b"invalid data found" in output:
        return "rtsp_invalid_data"
    status = re.search(
        rb"(?:method (?:options|describe|setup|play) failed:|server returned)\s*([45][0-9]{2})\b",
        output,
    )
    if status is not None:
        return "rtsp_status_" + status.group(1).decode("ascii")
    return "stream_ended"


def _decoder_markers(stderr: bytes) -> tuple[str, ...]:
    """Expose only hard-coded failure labels, never text from a stream URL."""
    output = stderr.lower()
    return tuple(label for needle, label in _DECODER_MARKERS if needle in output)


def _decoder_terms(stderr: bytes) -> tuple[str, ...]:
    """Return bounded, fixed vocabulary only; arbitrary error text stays private."""
    words = (word.decode("ascii") for word in re.findall(rb"[a-z]+", stderr.lower()))
    return tuple(sorted(set(words) & _DECODER_TERMS))


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
    def __init__(self, spec: StreamSpec, ffmpeg_path: str,
                 frame_observer: Callable[[bytes], Awaitable[None]] | None = None):
        self.spec = spec
        self.ffmpeg_path = ffmpeg_path
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self._stderr = bytearray()
        self.stderr_seen = False
        self.exit_code: int | None = None
        self.error_markers: tuple[str, ...] = ()
        self.error_terms: tuple[str, ...] = ()
        self.first_frame = asyncio.Event()
        self.frame_count = 0
        self.latest_frame: bytes | None = None
        self.last_frame_at = 0.0
        self.failure: str | None = None
        self.frame_observer = frame_observer
        self.observer_task: asyncio.Task | None = None
        self._observer_frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self.frames_observed = 0
        self.frames_skipped = 0
        self.observer_failed = False

    @property
    def healthy(self) -> bool:
        return (self.process is not None and self.process.returncode is None
                and self.failure is None and self.frame_count > 0
                and time.monotonic() - self.last_frame_at < 15)

    async def start(self, timeout: float) -> None:
        # The alias and destination have already passed exact policy checks.
        # FFmpeg may print the private alias. Drain stderr without logging it,
        # retaining only a short in-memory excerpt for fixed-code classification.
        self.process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-timeout", "5000000", "-i", self.spec.url,
            "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
            "-vf", "fps=1,scale=320:-2", "-threads", "1", "-f", "image2pipe",
            "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=_MAX_FRAME + 2,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        self.reader = asyncio.create_task(self._read_frames(), name="aiport-rtsp-frames")
        self.stderr_reader = asyncio.create_task(self._drain_stderr(), name="aiport-rtsp-stderr")
        if self.frame_observer is not None:
            self.observer_task = asyncio.create_task(
                self._observe_frames(), name="aiport-frame-observer")
        try:
            await asyncio.wait_for(self.first_frame.wait(), timeout=timeout)
        except TimeoutError as exc:
            await self.close()
            raise IngressError("stream_start_timeout") from exc
        if not self.healthy:
            reason = self.failure or "stream_unavailable"
            if reason == "stream_ended" and self.process is not None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.process.wait(), timeout=0.5)
                if self.stderr_reader is not None:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.stderr_reader, timeout=0.5)
                reason = _decoder_failure(bytes(self._stderr))
                self.error_markers = _decoder_markers(bytes(self._stderr))
                self.error_terms = _decoder_terms(bytes(self._stderr))
            if self.process is not None:
                self.exit_code = self.process.returncode
            await self.close()
            raise IngressError(reason)
        self._stderr.clear()

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while chunk := await self.process.stderr.read(4096):
            self.stderr_seen = True
            remaining = _MAX_DECODER_DIAGNOSTIC - len(self._stderr)
            if self.frame_count == 0 and remaining > 0:
                self._stderr.extend(chunk[:remaining])

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
                if self.frame_observer is not None and not self.observer_failed:
                    try:
                        self._observer_frames.put_nowait(frame)
                    except asyncio.QueueFull:
                        self.frames_skipped += 1
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            self.failure = "stream_ended"
        except asyncio.CancelledError:
            raise
        finally:
            self.first_frame.set()

    async def _observe_frames(self) -> None:
        assert self.frame_observer is not None
        try:
            while True:
                frame = await self._observer_frames.get()
                try:
                    await self.frame_observer(frame)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A model error must never stop stream-control responses or
                    # publish a partial detection. No private output is logged.
                    self.observer_failed = True
                    return
                self.frames_observed += 1
        except asyncio.CancelledError:
            raise

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
        if self.stderr_reader is not None:
            self.stderr_reader.cancel()
            await asyncio.gather(self.stderr_reader, return_exceptions=True)
        if self.observer_task is not None:
            self.observer_task.cancel()
            await asyncio.gather(self.observer_task, return_exceptions=True)
        self.reader = None
        self.stderr_reader = None
        self.observer_task = None
        self.process = None
        self.latest_frame = None
        self._stderr.clear()


class AiPortIngress:
    """Start only an allowed RTSP stream and confirm a decoded frame first."""

    def __init__(self, *, camera_mac: str, source_ip: str, ffmpeg_path: str,
                 start_timeout: float = 7,
                 frame_observer: Callable[[bytes], Awaitable[None]] | None = None):
        self.camera_mac = normalize_mac(camera_mac)
        self.source_ip = private_source_ip(source_ip)
        self.ffmpeg_path = executable_path(ffmpeg_path)
        if not 0 < start_timeout < 10:
            raise IngressError("invalid_start_timeout")
        self.start_timeout = start_timeout
        self.frame_observer = frame_observer
        self._session: _Session | None = None
        self._desired_spec: StreamSpec | None = None
        self._restart_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.total_frames_decoded = 0
        self.last_decoder_exit_code: int | None = None
        self.last_decoder_stderr_seen = False
        self.last_decoder_error_markers: tuple[str, ...] = ()
        self.last_decoder_error_terms: tuple[str, ...] = ()
        self.total_frames_observed = 0
        self.total_frames_skipped = 0
        self.observer_failures = 0
        self.restart_attempts = 0
        self.restart_successes = 0

    async def control(self, payload: object) -> dict:
        if not isinstance(payload, dict) or "streaming" not in payload:
            raise IngressError("invalid_stream_command")
        if payload["streaming"] is False:
            if set(payload) != {"streaming", "deviceID"}:
                raise IngressError("invalid_stream_command")
            if normalize_mac(payload["deviceID"]) != self.camera_mac:
                raise IngressError("camera_not_authorized")
            restart_task = self._restart_task
            if restart_task is not None:
                restart_task.cancel()
            async with self._lock:
                self._desired_spec = None
                self._restart_task = None
                await self._close_locked()
            if restart_task is not None:
                await asyncio.gather(restart_task, return_exceptions=True)
            return {"status": "stopped", "usedPoints": 0}
        spec = _stream_spec(payload, camera_mac=self.camera_mac, source_ip=self.source_ip)
        async with self._lock:
            if self._session is not None:
                if self._session.spec == spec and self._session.healthy:
                    return {"status": "started", "usedPoints": spec.points}
            if self._restart_task is not None:
                self._restart_task.cancel()
                self._restart_task = None
            if self._session is not None:
                await self._close_locked()
            self._desired_spec = None
            session = _Session(spec, self.ffmpeg_path, self.frame_observer)
            try:
                await session.start(self.start_timeout)
            except (OSError, IngressError) as exc:
                self.last_decoder_exit_code = session.exit_code
                self.last_decoder_stderr_seen = session.stderr_seen
                self.last_decoder_error_markers = session.error_markers
                self.last_decoder_error_terms = session.error_terms
                await session.close()
                if isinstance(exc, IngressError):
                    raise
                raise IngressError("stream_decoder_unavailable") from exc
            self.last_decoder_exit_code = None
            self.last_decoder_stderr_seen = False
            self.last_decoder_error_markers = ()
            self.last_decoder_error_terms = ()
            self._session = session
            self._desired_spec = spec
            self._restart_task = asyncio.create_task(self._watch_decoder())
            return {"status": "started", "usedPoints": spec.points}

    async def _watch_decoder(self) -> None:
        """Restore an accepted stream after a decoder exits or stops yielding frames."""
        delay = 1
        while True:
            await asyncio.sleep(delay)
            async with self._lock:
                spec = self._desired_spec
                if spec is None:
                    return
                if self._session is not None and self._session.healthy:
                    delay = 1
                    continue
                await self._close_locked()
                session = _Session(spec, self.ffmpeg_path, self.frame_observer)
                self.restart_attempts += 1
                try:
                    await session.start(self.start_timeout)
                except asyncio.CancelledError:
                    await session.close()
                    raise
                except (OSError, IngressError):
                    self.last_decoder_exit_code = session.exit_code
                    self.last_decoder_stderr_seen = session.stderr_seen
                    self.last_decoder_error_markers = session.error_markers
                    self.last_decoder_error_terms = session.error_terms
                    await session.close()
                    delay = min(delay * 2, 30)
                    continue
                self.last_decoder_exit_code = None
                self.last_decoder_stderr_seen = False
                self.last_decoder_error_markers = ()
                self.last_decoder_error_terms = ()
                self._session = session
                self.restart_successes += 1
                delay = 1

    def list_streams(self) -> list[dict]:
        session = self._session
        if session is None or not session.healthy:
            return []
        return [{"deviceID": session.spec.device_id, "points": session.spec.points}]

    def latest_frame(self) -> bytes | None:
        session = self._session
        return session.latest_frame if session is not None and session.healthy else None

    @property
    def reserved_points(self) -> int:
        """Count a decoder against capacity even if its frames have stalled."""
        return self._desired_spec.points if self._desired_spec is not None else 0

    @property
    def frame_count(self) -> int:
        return self._session.frame_count if self._session is not None else 0

    @property
    def streams_with_decoded_frames(self) -> int:
        """Count only a currently healthy stream that decoded a frame."""
        return int(bool(self.list_streams()) and self.frame_count > 0)

    @property
    def frames_observed(self) -> int:
        return (self.total_frames_observed +
                (self._session.frames_observed if self._session else 0))

    @property
    def frames_skipped(self) -> int:
        return (self.total_frames_skipped +
                (self._session.frames_skipped if self._session else 0))

    @property
    def observer_failed(self) -> bool:
        return self.observer_failures > 0 or bool(self._session and self._session.observer_failed)

    async def _close_locked(self) -> None:
        if self._session is not None:
            await self._session.close()
            self.total_frames_decoded += self._session.frame_count
            self.total_frames_observed += self._session.frames_observed
            self.total_frames_skipped += self._session.frames_skipped
            self.observer_failures += int(self._session.observer_failed)
            self._session = None

    async def close(self) -> None:
        restart_task = self._restart_task
        if restart_task is not None:
            restart_task.cancel()
        async with self._lock:
            self._desired_spec = None
            self._restart_task = None
            await self._close_locked()
        if restart_task is not None:
            await asyncio.gather(restart_task, return_exceptions=True)


class AiPortIngressPool:
    """Route bounded camera streams without exceeding one AI Port budget.

    This manager does not authorize cameras on its own. Every camera and RTSP
    source must appear in the explicit operator policy passed at construction.
    A device has ten capacity points: HD costs two, 2K three, and 4K five.
    """

    def __init__(self, policies: list[dict[str, str]], *,
                 frame_observer_factory: Callable[
                     [str], Callable[[bytes], Awaitable[None]] | None] | None = None):
        if not isinstance(policies, list) or not 1 <= len(policies) <= 5:
            raise IngressError("invalid_camera_pool")
        self._ingresses: dict[str, AiPortIngress] = {}
        for policy in policies:
            if not isinstance(policy, dict) or set(policy) != {
                    "camera_mac", "source_ip", "ffmpeg_path"}:
                raise IngressError("invalid_camera_pool")
            camera_mac = normalize_mac(policy["camera_mac"])
            if camera_mac in self._ingresses:
                raise IngressError("duplicate_camera")
            observer = (frame_observer_factory(camera_mac)
                        if frame_observer_factory is not None else None)
            self._ingresses[camera_mac] = AiPortIngress(
                camera_mac=camera_mac, source_ip=policy["source_ip"],
                ffmpeg_path=policy["ffmpeg_path"], frame_observer=observer)
        self._lock = asyncio.Lock()

    async def control(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise IngressError("invalid_stream_command")
        camera_mac = normalize_mac(payload.get("deviceID"))
        ingress = self._ingresses.get(camera_mac)
        if ingress is None:
            raise IngressError("camera_not_authorized")
        async with self._lock:
            if payload.get("streaming") is True:
                spec = _stream_spec(payload, camera_mac=camera_mac,
                                    source_ip=ingress.source_ip)
                used = sum(item.reserved_points for item in self._ingresses.values())
                if used - ingress.reserved_points + spec.points > 10:
                    raise IngressError("stream_capacity_exceeded")
            return await ingress.control(payload)

    def list_streams(self) -> list[dict]:
        return [stream for _, ingress in sorted(self._ingresses.items())
                for stream in ingress.list_streams()]

    @property
    def reserved_points(self) -> int:
        return sum(ingress.reserved_points for ingress in self._ingresses.values())

    @property
    def restart_attempts(self) -> int:
        return sum(ingress.restart_attempts for ingress in self._ingresses.values())

    @property
    def restart_successes(self) -> int:
        return sum(ingress.restart_successes for ingress in self._ingresses.values())

    @property
    def frame_count(self) -> int:
        return sum(ingress.frame_count for ingress in self._ingresses.values())

    @property
    def streams_with_decoded_frames(self) -> int:
        return sum(ingress.streams_with_decoded_frames
                   for ingress in self._ingresses.values())

    @property
    def total_frames_decoded(self) -> int:
        return sum(ingress.total_frames_decoded for ingress in self._ingresses.values())

    @property
    def frames_observed(self) -> int:
        return sum(ingress.frames_observed for ingress in self._ingresses.values())

    @property
    def frames_skipped(self) -> int:
        return sum(ingress.frames_skipped for ingress in self._ingresses.values())

    @property
    def observer_failed(self) -> bool:
        return any(ingress.observer_failed for ingress in self._ingresses.values())

    @property
    def last_decoder_exit_code(self) -> None:
        # A single exit code cannot identify which of several decoders failed.
        return None

    @property
    def last_decoder_stderr_seen(self) -> bool:
        return any(ingress.last_decoder_stderr_seen for ingress in self._ingresses.values())

    @property
    def last_decoder_error_markers(self) -> tuple[str, ...]:
        return tuple(sorted({marker for ingress in self._ingresses.values()
                             for marker in ingress.last_decoder_error_markers}))

    @property
    def last_decoder_error_terms(self) -> tuple[str, ...]:
        return tuple(sorted({term for ingress in self._ingresses.values()
                             for term in ingress.last_decoder_error_terms}))

    async def close(self) -> None:
        async with self._lock:
            for ingress in self._ingresses.values():
                await ingress.close()

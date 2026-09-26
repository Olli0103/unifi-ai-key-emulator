"""Speech-to-text adapter for the native AI Key ``speechToText`` task.

Protect (7.3.60 bundle; vendor AI Key 2.2.8) creates this task only for an
audio event whose detections include ``alrmSpeak``. It sends the AI Key an
audio export of the event and accepts ``{camera, event, stt:[{startMs, endMs,
text}]}`` at ``/internal/aiprocessors/speech-to-text``, where the times are
absolute epoch milliseconds. It stores each segment as a transcription row.

This module builds the transcription request for an explicitly selected
backend and turns its reply into bounded segments. It performs no HTTP and
never invents text: a reply without usable speech yields no segments, and an
unusable reply raises ``SpeechError`` so that nothing is posted.
"""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlsplit


class SpeechError(RuntimeError):
    """The speech backend configuration or its reply cannot produce a transcript."""


_OFFICIAL = "https://api.openai.com/v1"
_LANGUAGE = re.compile(r"[a-z]{2}\Z")
_MAX_SEGMENTS = 200
_MAX_SEGMENT_TEXT = 1000
# Whisper segment confidence: treat a segment as silence when the model says
# it probably contains no speech (the vendor agent ships the same idea).
_NO_SPEECH = 0.6
_UNCERTAIN = "[inaudible]"


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _private(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return address.is_private and not (address.is_loopback or address.is_link_local
                                       or address.is_multicast or address.is_unspecified)


class SpeechProvider:
    """``openai`` (official endpoint) or a loopback ``openai-compatible`` server."""

    def __init__(self, config: dict, *, lab: bool = False, require_api_key: bool = True):
        if not isinstance(config, dict):
            raise SpeechError("speech_to_text must be an object")
        self.provider = config.get("provider")
        if self.provider not in {"openai", "openai-compatible"}:
            raise SpeechError("speech_to_text.provider must be openai or openai-compatible")
        model = config.get("model")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise SpeechError("speech_to_text.model is required")
        self.model = model.strip()
        base = config.get("base_url") or (_OFFICIAL if self.provider == "openai" else None)
        if not isinstance(base, str):
            raise SpeechError("speech_to_text.base_url is required for openai-compatible")
        self.base_url = base.rstrip("/")
        try:
            url = urlsplit(self.base_url)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                    or url.password or url.query or url.fragment
                    or any(ch.isspace() or ch in "\\%" for ch in self.base_url)):
                raise ValueError
            port = url.port or (443 if url.scheme == "https" else 80)
        except (ValueError, TypeError) as exc:
            raise SpeechError("Invalid speech_to_text.base_url") from exc
        if self.provider == "openai":
            official = (url.scheme == "https" and url.hostname == "api.openai.com"
                        and port == 443 and url.path == "/v1")
            if not (official or (lab and _loopback(url.hostname))):
                raise SpeechError("OpenAI speech requires https://api.openai.com/v1")
        elif not (_loopback(url.hostname)
                  or config.get("local_network") is True and _private(url.hostname)):
            # Audio from a home camera goes only to the official API or a local
            # server: loopback, or an explicitly allowed private address such as
            # a sibling container on the host-only container network.
            raise SpeechError("openai-compatible speech must be a loopback or explicitly "
                              "allowed private-network server")
        self.url = self.base_url + "/audio/transcriptions"
        language = config.get("language")
        if language is not None and (not isinstance(language, str)
                                     or not _LANGUAGE.fullmatch(language)):
            raise SpeechError("speech_to_text.language must be a two-letter code")
        self.language = language
        self.headers = {}
        key = config.get("api_key")
        if key is not None:
            if not isinstance(key, str) or not key.strip() or any(ch.isspace() for ch in key.strip()):
                raise SpeechError("Invalid speech_to_text.api_key")
            self.headers["Authorization"] = "Bearer " + key.strip()
        elif self.provider == "openai" and require_api_key:
            raise SpeechError("OpenAI speech requires speech_to_text.api_key_file")

    def form_fields(self) -> list[tuple[str, str]]:
        """Multipart fields besides the audio file."""
        fields = [("model", self.model), ("response_format", "verbose_json"),
                  ("timestamp_granularities[]", "segment"), ("temperature", "0")]
        if self.language:
            fields.append(("language", self.language))
        return fields

    def parse(self, reply: Any, clip_ms: int) -> list[tuple[int, int, str]]:
        """Segments as (start, end) offsets in ms inside the clip, plus text."""
        if not isinstance(reply, dict) or type(clip_ms) is not int or clip_ms <= 0:
            raise SpeechError("Speech backend reply is not a transcription object")
        raw = reply.get("segments")
        if raw is None:
            text = reply.get("text")
            if not isinstance(text, str):
                raise SpeechError("Speech backend reply has neither segments nor text")
            raw = [{"start": 0, "end": clip_ms / 1000, "text": text}]
        if not isinstance(raw, list) or len(raw) > _MAX_SEGMENTS:
            raise SpeechError("Speech backend returned an unusable segment list")
        segments = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise SpeechError("Speech backend returned a malformed segment")
            start, end = item.get("start"), item.get("end")
            if (type(start) not in {int, float} or type(end) not in {int, float}
                    or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start):
                raise SpeechError("Speech backend returned invalid segment times")
            text = " ".join(item["text"].split())
            probability = item.get("no_speech_prob")
            if not text or (type(probability) in {int, float} and probability >= _NO_SPEECH):
                continue
            if len(text) > _MAX_SEGMENT_TEXT:
                text = text[:_MAX_SEGMENT_TEXT].rstrip() + " " + _UNCERTAIN
            begin = min(round(start * 1000), clip_ms)
            segments.append((begin, max(begin, min(round(end * 1000), clip_ms)), text))
        # Three identical consecutive segments are a known Whisper hallucination
        # on silence or noise; report no speech rather than repeated text.
        for index in range(len(segments) - 2):
            if segments[index][2] == segments[index + 1][2] == segments[index + 2][2]:
                return []
        return segments


def validate_speech_config(config: dict, *, lab: bool = False,
                           require_api_key: bool = True) -> tuple[SpeechProvider, frozenset[str]]:
    """The provider plus the explicit camera allowlist (Protect camera IDs)."""
    options = {key: value for key, value in config.items()
               if key not in {"camera_ids", "api_key_file", "max_audio_ms"}}
    provider = SpeechProvider(options, lab=lab, require_api_key=require_api_key)
    cameras = config.get("camera_ids")
    if (not isinstance(cameras, list) or not 1 <= len(cameras) <= 32
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", c)
                   for c in cameras) or len(set(cameras)) != len(cameras)):
        raise SpeechError("speech_to_text.camera_ids must list 1 to 32 Protect camera IDs")
    return provider, frozenset(cameras)

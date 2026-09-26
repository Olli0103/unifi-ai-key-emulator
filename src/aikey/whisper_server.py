"""Local speech-to-text server for the AI Key's ``speech_to_text`` backend.

Serves the OpenAI-compatible ``POST /v1/audio/transcriptions`` route (the
subset ``aikey.speech`` uses) with a local whisper.cpp model through
``pywhispercpp``. Audio never leaves the machine. The server accepts only
bounded 16 kHz mono 16-bit PCM WAV, runs one transcription at a time and
never logs or stores audio or text; it keeps only request counters.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import wave
from typing import Callable

from aiohttp import web


_MAX_AUDIO_BYTES = 32 * 120_000 + 4096     # 120 s of 16 kHz 16-bit mono, plus header
_LANGUAGE = re.compile(r"[a-z]{2}\Z")

# (samples as float32 array, language or "auto") -> [(start_s, end_s, text)], language
Transcriber = Callable[[object, str], tuple[list[tuple[float, float, str]], str | None]]


class AudioError(ValueError):
    """The upload is not bounded 16 kHz mono PCM WAV."""


def read_wav(data: bytes):
    import numpy
    try:
        with wave.open(io.BytesIO(data)) as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or audio.getframerate() != 16000 or audio.getcomptype() != "NONE"):
                raise AudioError("Expected 16 kHz mono 16-bit PCM WAV")
            frames = audio.readframes(audio.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError("Unreadable WAV") from exc
    if not frames:
        raise AudioError("Empty audio")
    return numpy.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0


def whisper_cpp_transcriber(model_path: str, threads: int) -> Transcriber:
    from pywhispercpp.model import Model
    model = Model(model_path, n_threads=threads, print_progress=False,
                  print_realtime=False, print_timestamps=False,
                  redirect_whispercpp_logs_to=None)

    def transcribe(samples, language):
        segments = model.transcribe(samples, language=language, translate=False)
        # pywhispercpp times are in 10 ms units.
        return ([(s.t0 / 100, s.t1 / 100, s.text) for s in segments],
                None if language == "auto" else language)
    return transcribe


def build_app(transcribe: Transcriber, *, default_language: str = "auto") -> web.Application:
    lock = asyncio.Lock()
    counters = {"requests": 0, "transcribed": 0, "rejected": 0, "failed": 0}

    async def transcriptions(request: web.Request) -> web.Response:
        counters["requests"] += 1
        if request.content_type != "multipart/form-data":
            counters["rejected"] += 1
            return web.json_response({"error": "multipart_required"}, status=400)
        fields, audio = {}, None
        try:
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "file":
                    audio = await part.read(decode=False)
                    if len(audio) > _MAX_AUDIO_BYTES:
                        raise AudioError("Audio too long")
                elif part.name in {"model", "response_format", "language", "temperature",
                                   "timestamp_granularities[]"}:
                    fields[part.name] = (await part.read(decode=False))[:64].decode("ascii", "replace")
            if audio is None:
                raise AudioError("Missing file")
            samples = read_wav(audio)
        except (AudioError, ValueError):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_audio"}, status=400)
        language = fields.get("language") or default_language
        if language != "auto" and not _LANGUAGE.fullmatch(language):
            counters["rejected"] += 1
            return web.json_response({"error": "invalid_language"}, status=400)
        async with lock:
            try:
                segments, detected = await asyncio.to_thread(transcribe, samples, language)
            except Exception:
                counters["failed"] += 1
                return web.json_response({"error": "transcription_failed"}, status=500)
        counters["transcribed"] += 1
        body = {"text": " ".join(text.strip() for _, _, text in segments).strip(),
                "segments": [{"start": start, "end": end, "text": text}
                             for start, end, text in segments],
                "duration": len(samples) / 16000}
        if detected:
            body["language"] = detected
        return web.json_response(body)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", **counters})

    app = web.Application(client_max_size=_MAX_AUDIO_BYTES + 65536)
    app.router.add_post("/v1/audio/transcriptions", transcriptions)
    app.router.add_get("/healthz", health)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local-whisper-server")
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8178)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--language", default="auto")
    args = parser.parse_args(argv)
    if args.language != "auto" and not _LANGUAGE.fullmatch(args.language):
        parser.error("--language must be auto or a two-letter code")
    app = build_app(whisper_cpp_transcriber(args.model, args.threads),
                    default_language=args.language)
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
